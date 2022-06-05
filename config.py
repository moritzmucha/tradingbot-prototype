import pytz
import json
import time
import sys
from binance.enums import KLINE_INTERVAL_1HOUR

ASSET = 'BTC'
QUOTE_ASSET = 'USDT'
QTY_DEC_PLACES = 6
PRICE_DEC_PLACES = 2
SYMBOL = ASSET + QUOTE_ASSET
INTERVAL = KLINE_INTERVAL_1HOUR
DATAFRAME_LENGTH = 1024
DATA_PATH = './data/{:s}_{:s}_{:d}.pkl'.format(SYMBOL, INTERVAL, DATAFRAME_LENGTH)
STATE_FILE_PATH = './data/state.json'
CREDENTIALS_FILE_PATH = './data/credentials.json'
MODEL_DIR = './models/'
MODEL_PATH_V01 = MODEL_DIR + 'grid_v01_7.pkl'
MODEL_PATH_V04 = MODEL_DIR + 'grid_v04_4.pkl'
TIMEZONE = 'CET'
TIMEZONE_OBJ = pytz.timezone(TIMEZONE)
TIMESTAMP_FORMAT = '[%m/%d %H:%M:%S]'
DATETIME_FORMAT_INTERNAL = '%Y-%m-%d %H:%M:%S %z'

LOOKAHEAD_WINDOW = 10
N_ROWS_TO_PREDICT = 24
PREDICTION_MA_WINDOW = 2

SIGNAL_THRESHOLD = 0.05

SL_ATR_FACTOR = 2.0
SL_PCT_OFFSET = 15.0
SL_TIMEOUT_ENABLED = True
SL_TIMEOUT_HOURS = 2

SHADOW_LIMIT_ENABLED = True
BUY_DELTA_A = 0.0
BUY_DELTA_B = 0.0
BUY_DELTA_C = 0.0
SELL_DELTA_A = 0.0
SELL_DELTA_B = 0.0
SELL_DELTA_C = 0.0
DELTA_DECAY_FACTOR = 2.0

ORDER_TIMEOUT_SECONDS = 5
TICKS_BETWEEN_ORDER_UPDATES = 20

DATA_COLUMNS = [
    'open',
    'high',
    'low',
    'close',
    'volume',
    'quote_asset_vol',
    'no_of_trades',
    'taker_buy_base_vol',
    'taker_buy_quote_vol'
]

IGNORED_COLUMNS = [
    'quote_asset_vol',
    'no_of_trades',
    'taker_buy_quote_vol',
    'close_time'
]

SL_BASE_COL = 'ema10'
ATR10_COL = 'atr10'
ATR10_COL_V01 = 'atr'
LABEL_COL = 'lookahead_ma{:d}'.format(LOOKAHEAD_WINDOW)
Y_PRED_COL = LABEL_COL + '_predicted'
Y_PRED_MA_COL = Y_PRED_COL + '_ma{:d}'.format(PREDICTION_MA_WINDOW)

try:
    with open(CREDENTIALS_FILE_PATH, 'r') as fh:
        credentials = json.load(fh)
except Exception as e:
    print('error while loading credentials\n{:s}: {:s}'.format(type(e).__name__, str(e)))
    print('shutting down...\n', flush=True)
    time.sleep(10.)
    sys.exit(0)

BINANCE_KEY = credentials['binance_key']
TG_BOT_TOKEN = credentials['tg_bot_token']
TG_RECIPIENT = credentials['tg_recipient']
