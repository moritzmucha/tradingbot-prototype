print('initializing...', flush=True)

import pandas as pd
import numpy as np
import datetime as dt
import joblib
import time
import sys
import telegram_interface as tg  # import whole module to avoid circular reference breaking everything
from twisted.internet import reactor
from xgboost import XGBRegressor
from binance.websockets import BinanceSocketManager
from binance_interface import tsm
from bot_utils import (
    BUY_TYPE,
    SELL_TYPE,
    STOPLOSS_TYPE,
    OCO_SELL_TYPE,
    tznow,
    get_timestamp,
    rounddown,
    time_decay,
    set_system_time_from_ntp,
    print_exception_and_shutdown
)
from df_utils import (
    dfpickle,
    create_dataframe,
    create_dfml,
    apply_technicals_v01,
    apply_technicals_v04_update
)
from config import (
    QTY_DEC_PLACES,
    PRICE_DEC_PLACES,
    SYMBOL,
    INTERVAL,
    DATAFRAME_LENGTH,
    DATA_PATH,
    TIMEZONE_OBJ,
    N_ROWS_TO_PREDICT,
    PREDICTION_MA_WINDOW,
    SIGNAL_THRESHOLD,
    SL_ATR_FACTOR,
    SL_PCT_OFFSET,
    SL_TIMEOUT_ENABLED,
    SHADOW_LIMIT_ENABLED,
    DELTA_DECAY_FACTOR,
    TICKS_BETWEEN_ORDER_UPDATES,
    DATA_COLUMNS,
    IGNORED_COLUMNS,
    SL_BASE_COL,
    LABEL_COL,
    Y_PRED_COL,
    Y_PRED_MA_COL
)
if tsm.mode == 'v01':
    from config import (
        ATR10_COL_V01 as ATR10_COL,
        MODEL_PATH_V01 as MODEL_PATH
    )
else:
    from config import ATR10_COL
    if tsm.mode == 'v04':
        from config import MODEL_PATH_V04 as MODEL_PATH
    else:
        raise ValueError('Unknown mode encountered during initialization')

def process_message(msg: dict) -> None:
    global df, tick_counter, last_order_update_tick
    tick_counter += 1
    
    try:
        tsm.last_price = float(msg['k']['c'])
    except KeyError:
        ts = get_timestamp()
        tg_msg = 'Connection failed: did not receive data'
        print(ts, tg_msg)
        print(ts, 'trying to send telegram message...', end=' ', flush=True)
        try:
            tg.send(tg_msg)
            print('done')
        except Exception:
            print('failed')
        print(get_timestamp(), 'shutting down...\n', flush=True)
        reactor.stop()
        tg.updater.stop()
        sys.exit(0)
    
    if tsm.stoploss_order_active and float(msg['k']['l']) <= tsm.stoploss_level:
        time_index = df.index[-1]
        if SL_TIMEOUT_ENABLED and time_index >= tsm.stoploss_hit_timeout:
            tsm.set_stoploss_hit_timeout(time_index)
        tsm.check_and_process_order(STOPLOSS_TYPE, update_balances=True)
    
    if msg['k']['x']:
        sl_adjustment_req_flag = False
        
        if tick_counter != last_order_update_tick + 1:
            if tsm.buy_order_active:
                holdings_increased = tsm.check_and_process_order(BUY_TYPE)
                if tsm.stoploss_enabled and holdings_increased:
                    sl_adjustment_req_flag = True
            elif tsm.sell_order_active:
                tsm.check_and_process_order(SELL_TYPE)
        
        tick_counter = 1
        df_ = df.iloc[-1000:].loc[:, DATA_COLUMNS].copy()
        
        new_tick = pd.Series(
            {
                'open': msg['k']['o'],
                'high': msg['k']['h'],
                'low': msg['k']['l'],
                'close': msg['k']['c'],
                'volume': msg['k']['v'],
                'quote_asset_vol': msg['k']['q'],
                'no_of_trades': msg['k']['n'],
                'taker_buy_base_vol': msg['k']['V'],
                'taker_buy_quote_vol': msg['k']['Q']
            },
            name = dt.datetime.fromtimestamp(msg['k']['t'] // 1000),
            dtype = 'float64'
        )
        
        df_.index = df_.index.tz_localize(None)
        df_ = df_.append(new_tick)
        df_.index = df_.index.tz_localize(TIMEZONE_OBJ, ambiguous='infer')
        if tsm.mode == 'v01':
            df_ = apply_technicals_v01(df_)
        elif tsm.mode == 'v04':
            df_ = apply_technicals_v04_update(df_)
        else:
            raise ValueError('Unknown mode encountered during dataframe update process')
        
        df = df.append(df_.iloc[-1, :])
        
        dfml = create_dfml(df, tsm.mode, IGNORED_COLUMNS)
        dfml[Y_PRED_COL] = np.full(dfml.shape[0], np.nan)
        index_end = dfml.index[-N_ROWS_TO_PREDICT:]
        dfml.loc[index_end, Y_PRED_COL] = est.predict(
            dfml.iloc[-N_ROWS_TO_PREDICT:, :].loc[
                :,
                [col not in [LABEL_COL, Y_PRED_COL, Y_PRED_MA_COL] for col in dfml.columns]
            ]
        )
        dfml[Y_PRED_MA_COL] = dfml[Y_PRED_COL].ewm(
            alpha = 1./PREDICTION_MA_WINDOW,
            min_periods = PREDICTION_MA_WINDOW
        ).mean()
        
        tg.notify_new_prediction(
            df.iloc[-1]['high'],
            df.iloc[-1]['low'],
            df.iloc[-1]['close'],
            dfml.iloc[-1][Y_PRED_COL],
            dfml.iloc[-1][Y_PRED_MA_COL]
        )
        
        df = df.iloc[-DATAFRAME_LENGTH:, :].copy()
        dfpickle(df.loc[:, DATA_COLUMNS], DATA_PATH, print_timestamp=True)
        
        if tsm.trading_enabled and not (
            SL_TIMEOUT_ENABLED and df.index[-1] < tsm.stoploss_hit_timeout
        ):
            if tsm.position_open and tsm.stoploss_enabled:
                sl_adjustment_req_flag = tsm.update_stoploss_level(
                    df.iloc[-1][SL_BASE_COL],
                    df.iloc[-1][ATR10_COL],
                    SL_ATR_FACTOR,
                    SL_PCT_OFFSET,
                    override_condition = 'greater'
                )
            
            if dfml.iloc[-1][Y_PRED_MA_COL] < -SIGNAL_THRESHOLD:
                if tsm.buy_signal_flag:
                    tsm.deactivate_buy_signal()
                if tsm.buy_order_req_flag:
                    tsm.buy_order_req_flag = False
                if tsm.buy_order_active:
                    tsm.cancel_buy_order()
                if tsm.position_open and not (
                    tsm.sell_order_active or
                    tsm.sell_order_req_flag or
                    tsm.sell_signal_flag
                ):
                    if tsm.stoploss_order_active:
                        tsm.cancel_stoploss_order()
                    elif tsm.stoploss_order_req_flag:
                        tsm.stoploss_order_req_flag = False
                    tsm.update_asset_balance()
                    if SHADOW_LIMIT_ENABLED:
                        tsm.activate_sell_signal(df.iloc[-1]['close'], df.iloc[-1][ATR10_COL])
                    else:
                        tsm.sell_order_req_flag = True
                        tsm.sell_target_price = df.iloc[-1]['close']
                    sl_adjustment_req_flag = False
            
            elif dfml.iloc[-1][Y_PRED_MA_COL] > SIGNAL_THRESHOLD:
                if tsm.position_open and (
                    tsm.sell_order_active or
                    tsm.sell_order_req_flag or
                    tsm.sell_signal_flag
                ):
                    if tsm.sell_signal_flag:
                        tsm.deactivate_sell_signal()
                    if tsm.sell_order_req_flag:
                        tsm.sell_order_req_flag = False
                    if tsm.sell_order_active:
                        tsm.cancel_sell_order()
                    if tsm.stoploss_enabled:
                        tsm.stoploss_order_req_flag = not tsm.stoploss_order_active
                    sl_adjustment_req_flag = False
                if not tsm.position_full and not (
                    tsm.buy_order_active or
                    tsm.buy_order_req_flag or
                    tsm.buy_signal_flag
                ):
                    tsm.update_quote_asset_balance()
                    if SHADOW_LIMIT_ENABLED:
                        tsm.activate_buy_signal(df.iloc[-1]['close'], df.iloc[-1][ATR10_COL])
                    else:
                        tsm.buy_order_req_flag = True
                        tsm.buy_target_price = df.iloc[-1]['close']
                    if tsm.position_open and tsm.stoploss_enabled and not sl_adjustment_req_flag:
                        sl_adjustment_req_flag = tsm.update_stoploss_level(
                            df.iloc[-1][SL_BASE_COL],
                            df.iloc[-1][ATR10_COL],
                            SL_ATR_FACTOR,
                            SL_PCT_OFFSET,
                            override_condition = 'not_equal'
                        )
            
            if sl_adjustment_req_flag:
                if tsm.stoploss_order_active:
                    if tsm.stoploss_is_oco and tsm.sell_order_active:
                        tsm.cancel_stoploss_order(alert=False)
                        tsm.sell_order_req_flag = not tsm.sell_order_active
                    else:
                        tsm.cancel_stoploss_order(alert=False)
                        tsm.stoploss_order_req_flag = not tsm.stoploss_order_active
        
        try:
            set_system_time_from_ntp(timeout=0.1)
        except Exception:
            pass
    
    elif (  # dataframe outdated, kline closing tick missed
        dt.datetime.fromtimestamp(msg['E'] // 1000, dt.timezone.utc) > df.index[-1] + dt.timedelta(hours=2)
    ):
        ts = get_timestamp()
        tg_msg = 'Connection failed: missed last hourly closing tick'
        print(ts, tg_msg)
        print(ts, 'trying to send telegram message...', end=' ', flush=True)
        try:
            tg.send(tg_msg)
            print('done')
        except Exception:
            print('failed')
        print(get_timestamp(), 'shutting down...\n', flush=True)
        if tick_counter == 1: time.sleep(300)
        reactor.stop()
        tg.updater.stop()
        sys.exit(0)
    
    elif tick_counter % TICKS_BETWEEN_ORDER_UPDATES == 0 and (
        tsm.buy_order_active or
        tsm.sell_order_active
    ):
        last_order_update_tick = tick_counter
        if tsm.buy_order_active:
            holdings_increased = tsm.check_and_process_order(BUY_TYPE, update_balances=True)
            if holdings_increased and tsm.trading_enabled and tsm.stoploss_enabled:
                if tsm.stoploss_order_active:
                    tsm.cancel_stoploss_order()
                tsm.stoploss_order_req_flag = not tsm.stoploss_order_active
        elif tsm.sell_order_active:
            tsm.check_and_process_order(SELL_TYPE, update_balances=True)
    
    elif tsm.trading_enabled:
        now = tznow().timestamp()
        
        if tsm.buy_signal_flag:
            delta_t = now - tsm.buy_signal_time
            current_buy_price_delta = time_decay(
                tsm.buy_price_delta,
                DELTA_DECAY_FACTOR,
                delta_t
            )
            tsm.buy_target_price = round(
                tsm.buy_signal_price - current_buy_price_delta,
                PRICE_DEC_PLACES
            )
            if float(msg['k']['c']) <= tsm.buy_target_price:
                tsm.buy_signal_flag = False
                tsm.buy_order_req_flag = True
        
        elif tsm.sell_signal_flag:
            delta_t = now - tsm.sell_signal_time
            current_sell_price_delta = time_decay(
                tsm.sell_price_delta,
                DELTA_DECAY_FACTOR,
                delta_t
            )
            tsm.sell_target_price = round(
                tsm.sell_signal_price + current_sell_price_delta,
                PRICE_DEC_PLACES
            )
            if float(msg['k']['c']) >= tsm.sell_target_price:
                tsm.sell_signal_flag = False
                tsm.sell_order_req_flag = True
        
        if now > tsm.order_timeout:
            if tsm.buy_order_req_flag:
                success = tsm.place_and_process_order(
                    BUY_TYPE,
                    rounddown(
                        float(tsm.quote_asset_balance['free']),
                        PRICE_DEC_PLACES
                    ) / tsm.buy_target_price,
                    tsm.buy_target_price
                )
                tsm.buy_order_req_flag = not success
                if success and tsm.stoploss_enabled:
                    tsm.update_stoploss_level(
                        df.iloc[-1][SL_BASE_COL],
                        df.iloc[-1][ATR10_COL],
                        SL_ATR_FACTOR,
                        SL_PCT_OFFSET,
                        override_condition = 'not_equal'
                    )
            
            elif tsm.sell_order_req_flag:
                if tsm.stoploss_enabled and float(msg['k']['c']) < tsm.sell_target_price:
                    success = tsm.place_and_process_order(
                        OCO_SELL_TYPE,
                        float(tsm.asset_balance['free']),
                        tsm.sell_target_price
                    )
                else:
                    success = tsm.place_and_process_order(
                        SELL_TYPE,
                        float(tsm.asset_balance['free']),
                        tsm.sell_target_price
                    )
                tsm.sell_order_req_flag = not success
            
            elif tsm.stoploss_order_req_flag:
                tsm.update_asset_balance()
                success = tsm.place_and_process_order(
                    STOPLOSS_TYPE,
                    float(tsm.asset_balance['free']),
                    tsm.stoploss_level
                )
                tsm.stoploss_order_req_flag = not success
    
    if tsm.stoploss_enabled and tsm.position_open and not (
        tsm.sell_order_active or
        tsm.sell_order_req_flag or
        tsm.sell_signal_flag or
        tsm.stoploss_order_active or
        tsm.stoploss_order_req_flag
    ):
        tsm.stoploss_order_req_flag = True
    
    if tsm.unsaved_changes: tsm.save_state()

tick_counter = 0
last_order_update_tick = -1

try:
    set_system_time_from_ntp()
except Exception as e:
    error_msg = str(e).strip('()').split(', ')
    if error_msg[0] == '1314':
        print('warning: insufficient privileges to change system time', flush=True)

print('loading prediction model...', end=' ', flush=True)
try:
    est = joblib.load(MODEL_PATH)
    print('done', flush=True)
except Exception as e:
    print_exception_and_shutdown(e)

print('connecting to exchange and updating account data...', end=' ', flush=True)
try:
    asset_bal_old = rounddown(
        float(tsm.asset_balance['free']) + float(tsm.asset_balance['locked']),
        QTY_DEC_PLACES
    )
    tsm.update_asset_balance()
    tsm.update_quote_asset_balance()
    asset_bal_new = rounddown(
        float(tsm.asset_balance['free']) + float(tsm.asset_balance['locked']),
        QTY_DEC_PLACES
    )
    
    if tsm.buy_order_active: tsm.check_buy_order()
    if tsm.sell_order_active: tsm.check_sell_order()
    if tsm.stoploss_order_active: tsm.check_stoploss_order()
    
    if (
        tsm.trading_enabled and
        tsm.stoploss_enabled and
        tsm.stoploss_order_active and
        asset_bal_new > asset_bal_old
    ):
        tsm.cancel_stoploss_order()
        tsm.stoploss_order_req_flag = not tsm.stoploss_order_active
    
    tsm.save_state()
    print('done', flush=True)
except Exception as e:
    print_exception_and_shutdown(e)

try:
    df = create_dataframe(
        SYMBOL,
        INTERVAL,
        DATA_PATH,
        tsm.mode,
        start = str(tznow() - dt.timedelta(hours=DATAFRAME_LENGTH + 1))
    )
except Exception as e:
    print_exception_and_shutdown(e)

print('starting websocket listener...\n', flush=True)
bm = BinanceSocketManager(tsm.client)
conn_key = bm.start_kline_socket(SYMBOL, process_message, interval=INTERVAL)
bm.start()
tg.updater.start_polling()
