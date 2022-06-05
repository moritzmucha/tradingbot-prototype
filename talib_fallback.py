import numpy as np
import pandas as pd

def SMA(df, timeperiod=30):
    return df['close'].rolling(timeperiod).mean()

def EMA(df, timeperiod=30):
    return df['close'].ewm(span=timeperiod, min_periods=timeperiod).mean()

def VAR(df, timeperiod=5, ddof=0):
    return df['close'].rolling(timeperiod).var(ddof=ddof)

def STDDEV(df, timeperiod=5, ddof=0):
    return df['close'].rolling(timeperiod).std(ddof=ddof)

def ATR(df, timeperiod=14):
    dhl = np.abs((df['high'] - df['low']).to_numpy())
    dhc = np.abs((df['high'] - df['close'].shift(1)).to_numpy())
    dlc = np.abs((df['low'] - df['close'].shift(1)).to_numpy())
    true_range = pd.Series(np.array([dhl, dhc, dlc]).T.max(1), index=df.index)
    return true_range.ewm(alpha=1./timeperiod, min_periods=timeperiod).mean()

def RSI(df, timeperiod=14):
    diff = df['close'] - df['close'].shift(1)
    up = diff.where(diff >= 0., 0.)
    down = -diff.where(diff < 0., 0.)
    up_weighted_avg = up.ewm(alpha=1./timeperiod, min_periods=timeperiod).mean()
    down_weighted_avg = down.ewm(alpha=1./timeperiod, min_periods=timeperiod).mean()
    return 100. - 100. / (1. + up_weighted_avg / down_weighted_avg)

def STOCH(df, fastk_period=5, slowk_period=3, slowd_period=3):
    h = df['high'].rolling(fastk_period).max()
    l = df['low'].rolling(fastk_period).min()
    fastk = 100. * (df['close'] - l) / (h - l)
    slowk = fastk.rolling(slowk_period).mean()
    slowd = slowk.rolling(slowd_period).mean().to_numpy()
    slowk = slowk.to_numpy()
    return pd.DataFrame(np.array([slowk, slowd]).T, columns=['slowk', 'slowd'], index=df.index)

def STOCHRSI(df, timeperiod=14, fastk_period=5, fastd_period=3):
    rsi = RSI(df, timeperiod=timeperiod)
    h = rsi.rolling(fastk_period).max()
    l = rsi.rolling(fastk_period).min()
    fastk = 100. * (rsi - l) / (h - l)
    fastd = fastk.rolling(fastd_period).mean().to_numpy()
    fastk = fastk.to_numpy()
    return pd.DataFrame(np.array([fastk, fastd]).T, columns=['fastk', 'fastd'], index=df.index)

def CDLENGULFING(df):
    return 0

def CDLHAMMER(df):
    return 0

def CDLINVERTEDHAMMER(df):
    return 0

def CDLHARAMI(df):
    return 0

def CDLHANGINGMAN(df):
    return 0

def CDLMORNINGSTAR(df):
    return 0

def CDLEVENINGSTAR(df):
    return 0

def CDLSHOOTINGSTAR(df):
    return 0

def CDLSPINNINGTOP(df):
    return 0

def CDLDOJI(df):
    return 0

def CDLDOJISTAR(df):
    return 0

def CDLLONGLEGGEDDOJI(df):
    return 0

def CDLDRAGONFLYDOJI(df):
    return 0

def CDLGRAVESTONEDOJI(df):
    return 0
