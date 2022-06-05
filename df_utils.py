import os
import re
import pandas as pd
import numpy as np
import datetime as dt
from scipy.stats import linregress
from typing import Union
from binance.client import Client
try:
    import talib.abstract as ta
except ModuleNotFoundError:
    import talib_fallback as ta
from bot_utils import tznow, get_timestamp
from config import TIMEZONE_OBJ

def check_gz_extension(filename:str) -> str:
    if re.search('\.gz$', filename):
        return filename
    else:
        return filename + '.gz'

def dfpickle(df:pd.DataFrame, path:str, print_timestamp:bool=False) -> None:
    if print_timestamp:
        ts = get_timestamp() + ' '
    else:
        ts = ''
    print('{:s}saving data to {:s}...'.format(ts, path), end=' ', flush=True)
    df.to_pickle(path)
    print('done', flush=True)

def dfpickle_zip(df:pd.DataFrame, path:str, print_timestamp:bool=False) -> None:
    path = check_gz_extension(path)
    if print_timestamp:
        ts = get_timestamp() + ' '
    else:
        ts = ''
    print('{:s}saving data to {:s}...'.format(ts, path), end=' ', flush=True)
    df.to_pickle(path, compression='gzip')
    print('done', flush=True)

def dfunpickle(path:str, print_timestamp:bool=False) -> pd.DataFrame:
    if print_timestamp:
        ts = get_timestamp() + ' '
    else:
        ts = ''
    print('{:s}loading data from {:s}...'.format(ts, path), end=' ', flush=True)
    try:
        df = pd.read_pickle(path)
        print('done', flush=True)
    except FileNotFoundError:
        print("\n{:s}error: '{:s}' not found in {:s}".format(ts, path, os.getcwd()), flush=True)
        raise
    return df

def dfunpickle_zip(path:str, print_timestamp:bool=False) -> pd.DataFrame:
    path = check_gz_extension(path)
    if print_timestamp:
        ts = get_timestamp() + ' '
    else:
        ts = ''
    print('{:s}loading data from {:s}...'.format(ts, path), end=' ', flush=True)
    try:
        df = pd.read_pickle(path, compression='gzip')
        print('done', flush=True)
    except FileNotFoundError:
        print("\n{:s}error: '{:s}' not found in {:s}".format(ts, path, os.getcwd()), flush=True)
        raise
    return df

def download_dataframe(
    symbol: str,
    interval: str,
    path: str,
    start: str,
    end: Union[str, None],
    zip: bool
) -> pd.DataFrame:
    print('downloading new data...', end=' ', flush=True)
    client = Client('', '')
    klines = client.get_historical_klines(symbol, interval, start, end)
    print('done', flush=True)
    
    opening_times = list()
    for tick in klines:
        opening_times.append(dt.datetime.fromtimestamp(tick[0] // 1000))
    
    df = pd.DataFrame(
        klines,
        dtype = 'float64',
        index = opening_times,
        columns = [
            0,
            'open',
            'high',
            'low',
            'close',
            'volume',
            6,
            'quote_asset_vol',
            'no_of_trades',
            'taker_buy_base_vol',
            'taker_buy_quote_vol',
            11
        ]
    )
    df.drop(columns=[0, 6, 11], inplace=True)
    del klines, opening_times
    
    df.index = df.index.tz_localize(TIMEZONE_OBJ, ambiguous='infer')
    df.drop(df.index[-1], inplace=True)
    
    if zip:
        dfpickle_zip(df, path)
    else:
        dfpickle(df, path)
    return df

def load_and_verify_dataframe(path:str, start:str, end:Union[str,None], zip:bool) -> pd.DataFrame:
    if zip:
        df = dfunpickle_zip(path)
    else:
        df = dfunpickle(path)
    df = df.loc[start:end]
    assert df.shape[0] >= 1000, 'Too short'
    if end is None:
        assert df.index[-1] > tznow() - dt.timedelta(hours=2), 'Not up to date'
    return df

def create_dataframe(
    symbol: str,
    interval: str,
    path: str,
    mode: str,
    download: Union[str, bool] = 'auto',
    start: str = '2000-01-01',
    end: Union[str, None] = None,
    zip: bool = False
) -> pd.DataFrame:
    if download == 'auto':
        try:
            df = load_and_verify_dataframe(path, start, end, zip)
        except FileNotFoundError:
            df = download_dataframe(symbol, interval, path, start, end, zip)
        except AssertionError:
            print('error: dataframe too short or not up to date', flush=True)
            df = download_dataframe(symbol, interval, path, start, end, zip)
    
    elif download == True:
        df = download_dataframe(symbol, interval, path, start, end, zip)
    
    else:
        if zip:
            df = dfunpickle_zip(path)
        else:
            df = dfunpickle(path)
        df = df.loc[start:end]
    
    if mode == 'v01':
        df = apply_technicals_v01(df)
    elif mode == 'v04':
        df = apply_technicals_v04_full(df)
    else:
        raise ValueError('Unknown mode encountered in create_dataframe')
    
    return df

def linregress_trend(series:pd.Series) -> float:
    slope, intercept, r_value, p_value, std_err = linregress(np.arange(series.shape[0]), series)
    return slope * r_value**2

def apply_technicals_v01(df:pd.DataFrame) -> pd.DataFrame:
    df['rsi'] = ta.RSI(df, timeperiod=14)
    stoch = ta.STOCH(df)
    df['stoch_slowk'] = stoch.slowk
    df['stoch_slowd'] = stoch.slowd
    stochrsi = ta.STOCHRSI(df, timeperiod=14)
    df['stochrsi_fastk'] = stochrsi.fastk
    df['stochrsi_fastd'] = stochrsi.fastd
    df['sma10'] = ta.SMA(df, timeperiod=10)
    df['sma20'] = ta.SMA(df, timeperiod=20)
    df['sma50'] = ta.SMA(df, timeperiod=50)
    df['sma100'] = ta.SMA(df, timeperiod=100)
    df['sma200'] = ta.SMA(df, timeperiod=200)
    df['sma500'] = ta.SMA(df, timeperiod=500)
    df['sma1000'] = ta.SMA(df, timeperiod=1000)
    df['ema5'] = ta.EMA(df, timeperiod=5)
    df['ema10'] = ta.EMA(df, timeperiod=10)
    df['ema21'] = ta.EMA(df, timeperiod=21)
    df['ema34'] = ta.EMA(df, timeperiod=34)
    df['std'] = ta.STDDEV(df, timeperiod=20)
    df['atr'] = ta.ATR(df, timeperiod=10)
    df['engulfing'] = ta.CDLENGULFING(df)
    df['hammer'] = ta.CDLHAMMER(df)
    df['invertedhammer'] = ta.CDLINVERTEDHAMMER(df)
    df['harami'] = ta.CDLHARAMI(df)
    df['hangingman'] = ta.CDLHANGINGMAN(df)
    df['morningstar'] = ta.CDLMORNINGSTAR(df)
    df['eveningstar'] = ta.CDLEVENINGSTAR(df)
    df['shootingstar'] = ta.CDLSHOOTINGSTAR(df)
    df['spinningtop'] = ta.CDLSPINNINGTOP(df)
    df['doji'] = ta.CDLDOJI(df)
    df['dojistar'] = ta.CDLDOJISTAR(df)
    df['longleggeddoji'] = ta.CDLLONGLEGGEDDOJI(df)
    df['dragonflydoji'] = ta.CDLDRAGONFLYDOJI(df)
    df['gravestonedoji'] = ta.CDLGRAVESTONEDOJI(df)
    return df

def apply_technicals_v04_full(df:pd.DataFrame) -> pd.DataFrame:
    df['rsi'] = ta.RSI(df, timeperiod=14)
    df['rsi_trend'] = df['rsi'].rolling(10).apply(linregress_trend)
    df['ema10'] = ta.EMA(df, timeperiod=10)
    df['ema20'] = ta.EMA(df, timeperiod=21)
    df['sma50'] = ta.SMA(df, timeperiod=50)
    df['sma100'] = ta.SMA(df, timeperiod=100)
    df['sma200'] = ta.SMA(df, timeperiod=200)
    df['sma500'] = ta.SMA(df, timeperiod=500)
    df['sma1000'] = ta.SMA(df, timeperiod=1000)
    df['std20'] = ta.STDDEV(df, timeperiod=20)
    df['atr10'] = ta.ATR(df, timeperiod=10)
    df['atr100'] = ta.ATR(df, timeperiod=100)
    df['keltner_upper'] = df['ema20'] + 2 * df['atr10']
    df['keltner_lower'] = df['ema20'] - 2 * df['atr10']
    stoch = ta.STOCH(df, slowd_period=5)
    df['stoch_slowk'] = stoch.slowk
    df['stoch_slowd'] = stoch.slowd
    stochrsi = ta.STOCHRSI(df, timeperiod=14)
    df['stochrsi_fastk'] = stochrsi.fastk
    df['stochrsi_fastd'] = stochrsi.fastd
    return df

def apply_technicals_v04_update(df:pd.DataFrame) -> pd.DataFrame:
    df['rsi'] = ta.RSI(df, timeperiod=14)
    df.loc[df.index[-1], 'rsi_trend'] = linregress_trend(df.iloc[-10:]['rsi'])
    df['ema10'] = ta.EMA(df, timeperiod=10)
    df['ema20'] = ta.EMA(df, timeperiod=21)
    df['sma50'] = ta.SMA(df, timeperiod=50)
    df['sma100'] = ta.SMA(df, timeperiod=100)
    df['sma200'] = ta.SMA(df, timeperiod=200)
    df['sma500'] = ta.SMA(df, timeperiod=500)
    df['sma1000'] = ta.SMA(df, timeperiod=1000)
    df['std20'] = ta.STDDEV(df, timeperiod=20)
    df['atr10'] = ta.ATR(df, timeperiod=10)
    df['atr100'] = ta.ATR(df, timeperiod=100)
    df['keltner_upper'] = df['ema20'] + 2 * df['atr10']
    df['keltner_lower'] = df['ema20'] - 2 * df['atr10']
    stoch = ta.STOCH(df, slowd_period=5)
    df['stoch_slowk'] = stoch.slowk
    df['stoch_slowd'] = stoch.slowd
    stochrsi = ta.STOCHRSI(df, timeperiod=14)
    df['stochrsi_fastk'] = stochrsi.fastk
    df['stochrsi_fastd'] = stochrsi.fastd
    return df

def create_dfml(df:pd.DataFrame, mode:str, ignored_columns:list=[]) -> pd.DataFrame:
    dfml = df.loc[:, [col not in ignored_columns for col in df.columns]].copy()
    if mode == 'v01':
        dfml['open'] = (dfml['open'] / dfml['close'] - 1.) * 100.
        dfml['high'] = (dfml['high'] / dfml['close'] - 1.) * 100.
        dfml['low'] = (dfml['low'] / dfml['close'] - 1.) * 100.
        dfml['sma10'] = (dfml['sma10'] / dfml['close'] - 1.) * 100.
        dfml['sma20'] = (dfml['sma20'] / dfml['close'] - 1.) * 100.
        dfml['sma50'] = (dfml['sma50'] / dfml['close'] - 1.) * 100.
        dfml['sma100'] = (dfml['sma100'] / dfml['close'] - 1.) * 100.
        dfml['sma200'] = (dfml['sma200'] / dfml['close'] - 1.) * 100.
        dfml['sma500'] = (dfml['sma500'] / dfml['close'] - 1.) * 100.
        dfml['sma1000'] = (dfml['sma1000'] / dfml['close'] - 1.) * 100.
        dfml['ema5'] = (dfml['ema5'] / dfml['close'] - 1.) * 100.
        dfml['ema10'] = (dfml['ema10'] / dfml['close'] - 1.) * 100.
        dfml['ema21'] = (dfml['ema21'] / dfml['close'] - 1.) * 100.
        dfml['ema34'] = (dfml['ema34'] / dfml['close'] - 1.) * 100.
        dfml['std'] = (dfml['std'] / dfml['close'] - 1.) * 100.
        dfml['atr'] = (dfml['atr'] / dfml['close'] - 1.) * 100.
    elif mode == 'v04':
        dfml['open'] = dfml['open'] / dfml['close'] - 1.
        dfml['high'] = dfml['high'] / dfml['close'] - 1.
        dfml['low'] = dfml['low'] / dfml['close'] - 1.
        dfml['ema10'] = dfml['ema10'] / dfml['close'] - 1.
        dfml['ema20'] = dfml['ema20'] / dfml['close'] - 1.
        dfml['sma50'] = dfml['sma50'] / dfml['close'] - 1.
        dfml['sma100'] = dfml['sma100'] / dfml['close'] - 1.
        dfml['sma200'] = dfml['sma200'] / dfml['close'] - 1.
        dfml['sma500'] = dfml['sma500'] / dfml['close'] - 1.
        dfml['sma1000'] = dfml['sma1000'] / dfml['close'] - 1.
        dfml['std20'] = dfml['std20'] / dfml['close']
        dfml['atr10'] = dfml['atr10'] / dfml['close']
        dfml['atr100'] = dfml['atr100'] / dfml['close']
        dfml['keltner_upper'] = dfml['keltner_upper'] / dfml['close'] - 1.
        dfml['keltner_lower'] = dfml['keltner_lower'] / dfml['close'] - 1.
        normalized_columns = [
            'volume',
            'taker_buy_base_vol'
        ]
        mean_dict = {'volume': 1883.2653201563026, 'taker_buy_base_vol': 943.0650478388751}
        std_dict = {'volume': 2056.6755264848543, 'taker_buy_base_vol': 1011.2795091423222}
        for col in normalized_columns:
            dfml[col] = (dfml[col] - mean_dict[col]) / std_dict[col]
    else:
        raise ValueError('Unknown mode encountered in create_dfml')
    dfml.drop('close', axis=1, inplace=True)
    return dfml
