import sys
import datetime as dt
from twisted.internet import reactor
from telegram import Update
from telegram.bot import Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.constants import PARSEMODE_HTML
from binance_interface import tsm
from bot_utils import (
    BUY_TYPE, OCO_STOPLOSS_TYPE, SELL_TYPE, STOPLOSS_TYPE,
    ORDER_TYPE_DICT,
    get_timestamp,
    rounddown
)
from config import (
    ASSET,
    QUOTE_ASSET,
    QTY_DEC_PLACES,
    PRICE_DEC_PLACES,
    PREDICTION_MA_WINDOW,
    TG_RECIPIENT,
    TG_BOT_TOKEN
)

def send(*args, **kwargs) -> None:
    bot.send_message(TG_RECIPIENT, *args, **kwargs)

def bot_enable_trading(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        tsm.trading_enabled = True
        msg = 'Trading has been enabled'
        print(get_timestamp(), msg.lower(), flush=True)
        context.bot.send_message(TG_RECIPIENT, msg)

def bot_disable_trading(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        tsm.trading_enabled = False
        msg = 'Trading has been disabled'
        print(get_timestamp(), msg.lower(), flush=True)
        context.bot.send_message(TG_RECIPIENT, msg)

def bot_enable_stoploss(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        tsm.stoploss_enabled = True
        msg = 'Stop-loss has been enabled'
        print(get_timestamp(), msg.lower(), flush=True)
        context.bot.send_message(TG_RECIPIENT, msg)

def bot_disable_stoploss(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        tsm.stoploss_enabled = False
        msg = 'Stop-loss has been disabled'
        print(get_timestamp(), msg.lower(), flush=True)
        context.bot.send_message(TG_RECIPIENT, msg)

def bot_cancel_order(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        if tsm.buy_order_active: tsm.cancel_buy_order()
        if tsm.sell_order_active: tsm.cancel_sell_order()
        if tsm.stoploss_order_active: tsm.cancel_stoploss_order()

def bot_reset_flags(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        tsm.buy_signal_flag = False
        tsm.sell_order_req_flag = False
        tsm.stoploss_order_req_flag = False
        msg = 'Flags have been reset'
        print(get_timestamp(), msg.lower(), flush=True)
        context.bot.send_message(TG_RECIPIENT, msg)

def bot_restart(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        msg = 'Restarting script...'
        print(get_timestamp(), msg.lower(), flush=True)
        context.bot.send_message(TG_RECIPIENT, msg)
        reactor.stop()
        updater.stop()
        sys.exit(0)

def bot_switch_mode(update:Update, context:CallbackContext) -> None:
    available_modes = ('v01', 'v04')
    if update.effective_chat.id == TG_RECIPIENT:
        if len(context.args) > 0:
            desired_mode = context.args[0].lower()
            if desired_mode in available_modes:
                if desired_mode != tsm.mode:
                    tsm.mode = desired_mode
                    msg = 'Switching mode to {:s}...'.format(desired_mode)
                    print(get_timestamp(), msg.lower(), flush=True)
                    context.bot.send_message(TG_RECIPIENT, msg)
                    reactor.stop()
                    updater.stop()
                    sys.exit(0)
                else:
                    msg = 'Mode {:s} is already active'.format(tsm.mode)
                    context.bot.send_message(TG_RECIPIENT, msg)
            else:
                msg = 'Argument not recognized. Available modes: {:s}'.format(', '.join(available_modes))
                context.bot.send_message(TG_RECIPIENT, msg)
        else:
            msg = 'Current mode: {:s}'.format(tsm.mode)
            context.bot.send_message(TG_RECIPIENT, msg)

def bot_price_info(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        msg = 'Current price: {:.{:d}f} {:s}'.format(tsm.last_price, PRICE_DEC_PLACES, QUOTE_ASSET)
        context.bot.send_message(TG_RECIPIENT, msg)

def bot_print_help(update:Update, context:CallbackContext) -> None:
    if update.effective_chat.id == TG_RECIPIENT:
        available_commands = ', '.join(bot_commands.keys())
        msg = 'Available commands: {:s}'.format(available_commands)
        context.bot.send_message(TG_RECIPIENT, msg)

def bot_handle_error(update:Update, context:CallbackContext) -> None:
    msg = '{:s}'.format(type(context.error).__name__)
    print(get_timestamp(), msg.lower(), flush=True)

def notify(msg:str, **kwargs) -> None:
    print(get_timestamp(), msg.lower(), flush=True)
    send(msg, **kwargs)

def notify_order_placed(order_type:str, quantity:float, price:float, alert:bool=True) -> None:
    silent = order_type == STOPLOSS_TYPE
    order_id = tsm.get_order_id(order_type)
    msg = 'New {:s} order for {:.{:d}f} {:s}'.format(
            ORDER_TYPE_DICT[order_type],
            rounddown(quantity, QTY_DEC_PLACES),
            QTY_DEC_PLACES,
            ASSET
        ) \
        + ' at {:.{:d}f} {:s}'.format(price, PRICE_DEC_PLACES, QUOTE_ASSET) \
        + ' created (#{:s})'.format(str(order_id))
    print(get_timestamp(), msg.lower(), flush=True)
    if alert: send(msg, disable_notification=silent)

def notify_order_cancelled(order_type:str, alert:bool=True) -> None:
    silent = (order_type == STOPLOSS_TYPE or order_type == OCO_STOPLOSS_TYPE)
    order_id = tsm.get_order_id(order_type)
    order_name = ORDER_TYPE_DICT[order_type]
    msg = '{:s} order #{:s} has been cancelled'.format(
        order_name[0].upper() + order_name[1:],
        str(order_id)
    )
    print(get_timestamp(), msg.lower(), flush=True)
    if alert: send(msg, disable_notification=silent)

def notify_order_partially_filled(order_type:str, alert:bool=True) -> None:
    order_id = tsm.get_order_id(order_type)
    orig_qty = tsm.get_original_quantity(order_type)
    exec_qty = tsm.get_executed_quantity(order_type)
    cum_quote_qty = tsm.get_cumulative_quote_quantity(order_type)
    order_name = ORDER_TYPE_DICT[order_type]
    msg = '{:s} order #{:s} has been partially filled:'.format(
            order_name[0].upper() + order_name[1:],
            str(order_id)
        ) \
        + ' {:.{:d}f} {:s} for'.format(exec_qty, QTY_DEC_PLACES, ASSET) \
        + ' {:.{:d}f} {:s}'.format(cum_quote_qty, PRICE_DEC_PLACES, QUOTE_ASSET) \
        + ' ({:.1f}%)'.format(100. * exec_qty / orig_qty)
    print(get_timestamp(), msg.lower(), flush=True)
    if alert: send(msg)

def notify_order_filled(order_type:str, alert:bool=True) -> None:
    order_id = tsm.get_order_id(order_type)
    cum_quote_qty = tsm.get_cumulative_quote_quantity(order_type)
    order_name = ORDER_TYPE_DICT[order_type]
    msg = '{:s} order #{:s} has been filled'.format(
            order_name[0].upper() + order_name[1:],
            str(order_id)
        ) \
        + ' for {:.{:d}f} {:s}!'.format(cum_quote_qty, PRICE_DEC_PLACES, QUOTE_ASSET)
    print(get_timestamp(), msg.lower(), flush=True)
    if alert: send(msg)

def notify_unexpected_order_status(order_type:str, status:str) -> None:
    order_id = tsm.get_order_id(order_type)
    msg = 'Warning: {:s} order #{:s} returned status {:s}'.format(
        ORDER_TYPE_DICT[order_type],
        str(order_id),
        status
    )
    notify(msg)

def notify_new_prediction(
    high: float,
    low: float,
    close: float,
    prediction: float,
    prediction_ma: float
) -> None:
    print(get_timestamp(), 'new prediction: {:+.3f}% (ma2: {:+.3f}%)'.format(
        prediction,
        prediction_ma
    ), flush=True)
    msg = '<b>High:</b> {:.{:d}f} {:s}\n'.format(high, PRICE_DEC_PLACES, QUOTE_ASSET) \
        + '<b>Low:</b> {:.{:d}f} {:s}\n'.format(low, PRICE_DEC_PLACES, QUOTE_ASSET) \
        + '<b>Close:</b> {:.{:d}f} {:s}\n'.format(close, PRICE_DEC_PLACES, QUOTE_ASSET) \
        + '<b>Prediction:</b> {:+.3f}%\n'.format(prediction) \
        + '<b>Prediction MA{:d}:</b> {:+.3f}%'.format(PREDICTION_MA_WINDOW, prediction_ma)
    send(msg, parse_mode=PARSEMODE_HTML, disable_notification=True)

def notify_stoploss_update(stoploss_level:float) -> None:
    msg = 'Updating stop-loss level to {:.{:d}f} {:s}'.format(
        stoploss_level,
        PRICE_DEC_PLACES,
        QUOTE_ASSET
    )
    notify(msg, disable_notification=True)

def notify_stoploss_hit() -> None:
    reopen_time = tsm.stoploss_hit_timeout + dt.timedelta(hours=1)
    ts_format = '%Y-%m-%d %H:%M (%Z)'
    msg = 'Stop-loss has been hit at {:.{:d}f} {:s}!\n'.format(
        tsm.stoploss_level,
        PRICE_DEC_PLACES,
        QUOTE_ASSET
    ) + 'Trading paused until {:s}'.format(reopen_time.strftime(ts_format))
    notify(msg)

def notify_signal_activated(order_type:str) -> None:
    order_name = ORDER_TYPE_DICT[order_type]
    if order_type == BUY_TYPE:
        target_price = tsm.buy_signal_price - tsm.buy_price_delta
    elif order_type == SELL_TYPE:
        target_price = tsm.sell_signal_price + tsm.sell_price_delta
    else:
        raise ValueError('Unexpected order type encountered in notify_signal_activated')
    msg = '{:s} signal activated! Shadow limit currently at {:.{:d}f} {:s}'.format(
        order_name[0].upper() + order_name[1:],
        target_price,
        PRICE_DEC_PLACES,
        QUOTE_ASSET
    )
    notify(msg)

def notify_signal_deactivated(order_type:str) -> None:
    order_name = ORDER_TYPE_DICT[order_type]
    msg = '{:s} signal deactivated'.format(order_name[0].upper() + order_name[1:])
    notify(msg, disable_notification=True)

bot_commands = {
    'enable': bot_enable_trading,
    'disable': bot_disable_trading,
    'sl_enable': bot_enable_stoploss,
    'sl_disable': bot_disable_stoploss,
    'cancel': bot_cancel_order,
    'resetflags': bot_reset_flags,
    'restart': bot_restart,
    'mode': bot_switch_mode,
    'price': bot_price_info,
    'help': bot_print_help
}

bot = Bot(TG_BOT_TOKEN)
updater = Updater(TG_BOT_TOKEN)

for k, v in bot_commands.items():
    updater.dispatcher.add_handler(CommandHandler(k, v, run_async=True))

updater.dispatcher.add_error_handler(bot_handle_error, run_async=True)
