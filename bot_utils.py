import numpy as np
import datetime as dt
import time
import sys
import os
if os.name == 'nt':
    import win32api
from ntplib import NTPClient
from config import (
    TIMEZONE_OBJ,
    TIMESTAMP_FORMAT
)

BUY_TYPE = 'buy'
SELL_TYPE = 'sell'
STOPLOSS_TYPE = 'stoploss'
OCO_SELL_TYPE = 'oco_sell'
OCO_STOPLOSS_TYPE = 'oco_stoploss'

ORDER_TYPE_DICT = {
    BUY_TYPE: 'buy',
    SELL_TYPE: 'sell',
    STOPLOSS_TYPE: 'stop-loss',
    OCO_SELL_TYPE: 'OCO sell',
    OCO_STOPLOSS_TYPE: 'OCO stop-loss'
}

def check_order_type(order_type:str) -> None:
    if order_type not in ORDER_TYPE_DICT.keys():
        valid_types = str(tuple(ORDER_TYPE_DICT.keys()))
        error_msg = f'Order type argument for internal functions must be one of {valid_types:s}.'
        raise ValueError(error_msg)

def tznow() -> dt.datetime:
    return dt.datetime.now(TIMEZONE_OBJ)

def get_timestamp() -> str:
    return tznow().strftime(TIMESTAMP_FORMAT)

def rounddown(number:float, places:int) -> float:
    return np.floor(number * 10**places) / 10**places

def calculate_stoploss(x:float, d:float=0.0, k:float=2.0, pct_offset:float=15.0) -> float:
    return (x - k * d) * (1. - pct_offset / 100.)

def calculate_price_delta(x:float, a:float=0.0005, b:float=0.0, c:float=40.0) -> float:
    return a * x**2 + b * x + c

def time_decay(x:float, decay_factor:float, dt:float, time_unit:float=3600.) -> float:
    dt = dt - 0.5 * time_unit if dt > 0.5 * time_unit else 0.
    return x / np.exp(np.log(decay_factor) * dt / time_unit)

def set_system_time_from_ntp(
    ntp_server: str = 'europe.pool.ntp.org',
    version: int = 4,
    timeout: float = 0.3
) -> None:
    if os.name == 'nt':
        ntp_client = NTPClient()
        r = ntp_client.request(ntp_server, version=version, timeout=timeout)
        r_dt = dt.datetime.fromtimestamp(r.tx_time, tz=TIMEZONE_OBJ)
        r_dt = r_dt.astimezone(dt.timezone.utc)
        day_of_week = r_dt.isocalendar()[2]
        t = (
            r_dt.year,
            r_dt.month,
            day_of_week,
            r_dt.day,
            r_dt.hour,
            r_dt.minute,
            r_dt.second,
            r_dt.microsecond // 1000
        )
        win32api.SetSystemTime(*t)

def print_exception_and_shutdown(e:Exception, sleep_period:float=10.) -> None:
    print('\n{:s}: {:s}'.format(type(e).__name__, str(e)))
    print('shutting down...\n', flush=True)
    time.sleep(sleep_period)
    sys.exit(0)
