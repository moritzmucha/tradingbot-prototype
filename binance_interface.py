import re
import json
import datetime as dt
import telegram_interface as tg  # import whole module to avoid circular reference breaking everything
from typing import Union
from binance.client import Client
from binance.enums import (
    TIME_IN_FORCE_GTC,
    SIDE_SELL,
    ORDER_TYPE_STOP_LOSS_LIMIT,
    ORDER_STATUS_NEW,
    ORDER_STATUS_PARTIALLY_FILLED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_CANCELED,
    ORDER_STATUS_REJECTED,
    ORDER_STATUS_EXPIRED
)
from binance.exceptions import BinanceAPIException
from bot_utils import (
    BUY_TYPE,
    SELL_TYPE,
    STOPLOSS_TYPE,
    OCO_SELL_TYPE,
    OCO_STOPLOSS_TYPE,
    ORDER_TYPE_DICT,
    check_order_type,
    tznow,
    get_timestamp,
    rounddown,
    calculate_stoploss,
    calculate_price_delta,
    print_exception_and_shutdown
)
from config import (
    ASSET,
    QUOTE_ASSET,
    QTY_DEC_PLACES,
    PRICE_DEC_PLACES,
    SYMBOL,
    STATE_FILE_PATH,
    DATETIME_FORMAT_INTERNAL,
    SL_TIMEOUT_HOURS,
    BUY_DELTA_A,
    BUY_DELTA_B,
    BUY_DELTA_C,
    SELL_DELTA_A,
    SELL_DELTA_B,
    SELL_DELTA_C,
    ORDER_TIMEOUT_SECONDS,
    BINANCE_KEY
)

ERROR_CODE_INVALID_MESSAGE = -1013
ERROR_CODE_NEW_ORDER_REJECTED = -2010
ERROR_MESSAGE_INVALID_QUANTITY = 'Invalid quantity.'
ERROR_MESSAGE_OCO_PRICES_INCORRECT = 'The relationship of the prices for the orders is not correct.'

def _extract_api_error_code(e:Exception) -> Union[int, None]:
    if isinstance(e, BinanceAPIException):
        error_code_match = re.search(r'code=(-?\d+)', str(e))
        if error_code_match: return int(error_code_match.group(1))

class TradeStateMachine:
    def __init__(self) -> None:
        self.client = Client(*BINANCE_KEY)
        self.last_price = 0.0
        self.__unsaved_changes = False
        self.__state = self.load_state()
    
    def load_state(self) -> dict:
        with open(STATE_FILE_PATH, 'r') as fh:
            state = json.load(fh)
        sl_timeout_dt = dt.datetime.strptime(state['stoploss_hit_timeout'], DATETIME_FORMAT_INTERNAL)
        state['stoploss_hit_timeout'] = sl_timeout_dt
        return state
    
    def save_state(self) -> None:
        state = self.__state.copy()
        sl_timeout_txt = state['stoploss_hit_timeout'].strftime(DATETIME_FORMAT_INTERNAL)
        state['stoploss_hit_timeout'] = sl_timeout_txt
        with open(STATE_FILE_PATH, 'w', newline='\n') as fh:
            json.dump(state, fh, indent=2)
        self.__unsaved_changes = False
    
    def update_asset_balance(self) -> None:
        self.asset_balance = self.client.get_asset_balance(ASSET)
    
    def update_quote_asset_balance(self) -> None:
        self.quote_asset_balance = self.client.get_asset_balance(QUOTE_ASSET)
    
    def set_order_timeout(self) -> None:
        self.order_timeout = tznow().timestamp() + ORDER_TIMEOUT_SECONDS
    
    def set_stoploss_hit_timeout(self, starting_time:dt.datetime) -> None:
        self.stoploss_hit_timeout = starting_time + dt.timedelta(hours=SL_TIMEOUT_HOURS)
        tg.notify_stoploss_hit()
    
    def get_order_id(self, order_type:str) -> int:
        if order_type == BUY_TYPE:
            return self.buy_order_id
        elif order_type == SELL_TYPE or order_type == OCO_SELL_TYPE:
            return self.sell_order_id
        elif order_type == STOPLOSS_TYPE or order_type == OCO_STOPLOSS_TYPE:
            return self.stoploss_order_id
        else:
            check_order_type(order_type)
    
    def get_order_price(self, order_type:str) -> float:
        if order_type == BUY_TYPE:
            return float(self.buy_order_price)
        elif order_type == SELL_TYPE or order_type == OCO_SELL_TYPE:
            return float(self.sell_order_price)
        elif order_type == STOPLOSS_TYPE or order_type == OCO_STOPLOSS_TYPE:
            return float(self.stoploss_order_price)
        else:
            check_order_type(order_type)
    
    def get_original_quantity(self, order_type:str) -> float:
        if order_type == BUY_TYPE:
            return float(self.buy_order_original_qty)
        elif order_type == SELL_TYPE or order_type == OCO_SELL_TYPE:
            return float(self.sell_order_original_qty)
        elif order_type == STOPLOSS_TYPE or order_type == OCO_STOPLOSS_TYPE:
            return float(self.stoploss_order_original_qty)
        else:
            check_order_type(order_type)
    
    def get_executed_quantity(self, order_type:str) -> float:
        if order_type == BUY_TYPE:
            return float(self.buy_order_executed_qty)
        elif order_type == SELL_TYPE or order_type == OCO_SELL_TYPE:
            return float(self.sell_order_executed_qty)
        elif order_type == STOPLOSS_TYPE or order_type == OCO_STOPLOSS_TYPE:
            return float(self.stoploss_order_executed_qty)
        else:
            check_order_type(order_type)
    
    def get_cumulative_quote_quantity(self, order_type:str) -> float:
        if order_type == BUY_TYPE:
            return float(self.buy_order_cum_quote_qty)
        elif order_type == SELL_TYPE or order_type == OCO_SELL_TYPE:
            return float(self.sell_order_cum_quote_qty)
        elif order_type == STOPLOSS_TYPE or order_type == OCO_STOPLOSS_TYPE:
            return float(self.stoploss_order_cum_quote_qty)
        else:
            check_order_type(order_type)
    
    def place_buy_order(self, quantity:float, price:float, alert:bool=True) -> Union[str, int, None]:
        try:
            order = self.client.order_limit_buy(
                quantity = rounddown(quantity, QTY_DEC_PLACES),
                price = '{:.{:d}f}'.format(price, PRICE_DEC_PLACES),
                symbol = SYMBOL
            )
            self.buy_order_active = True
            self.buy_order_id = order['orderId']
            self.buy_order_price = order['price']
            self.buy_order_status = order['status']
            self.buy_order_original_qty = order['origQty']
            self.buy_order_executed_qty = order['executedQty']
            self.buy_order_cum_quote_qty = order['cummulativeQuoteQty']
            tg.notify_order_placed(BUY_TYPE, quantity, price, alert=alert)
            return order['status']
        except Exception as e:
            tg_msg = 'Warning: exception during attempt to place a buy order\n' \
                + '{:s}: {:s}'.format(type(e).__name__, str(e))
            tg.notify(tg_msg)
            return _extract_api_error_code(e)
    
    def place_sell_order(self, quantity:float, price:float, alert:bool=True) -> Union[str, int, None]:
        try:
            order = self.client.order_limit_sell(
                quantity = rounddown(quantity, QTY_DEC_PLACES),
                price = '{:.{:d}f}'.format(price, PRICE_DEC_PLACES),
                symbol = SYMBOL
            )
            self.stoploss_is_oco = False
            self.sell_order_active = True
            self.sell_order_id = order['orderId']
            self.sell_order_price = order['price']
            self.sell_order_status = order['status']
            self.sell_order_original_qty = order['origQty']
            self.sell_order_executed_qty = order['executedQty']
            self.sell_order_cum_quote_qty = order['cummulativeQuoteQty']
            tg.notify_order_placed(SELL_TYPE, quantity, price, alert=alert)
            return order['status']
        except Exception as e:
            error_code = _extract_api_error_code(e)
            if not (
                error_code == ERROR_CODE_INVALID_MESSAGE and
                str(e).endswith(ERROR_MESSAGE_INVALID_QUANTITY)
            ):
                tg_msg = 'Warning: exception during attempt to place a sell order\n' \
                    + '{:s}: {:s}'.format(type(e).__name__, str(e))
                tg.notify(tg_msg)
            return error_code
    
    def place_oco_sell_order(
        self,
        quantity: float,
        price: float,
        sl_price: float,
        alert: bool = True
    ) -> Union[str, int, None]:
        try:
            order = self.client.create_oco_order(
                side = SIDE_SELL,
                quantity = rounddown(quantity, QTY_DEC_PLACES),
                price = '{:.{:d}f}'.format(price, PRICE_DEC_PLACES),
                stopPrice = '{:.{:d}f}'.format(sl_price, PRICE_DEC_PLACES),
                stopLimitPrice = '{:.{:d}f}'.format(sl_price, PRICE_DEC_PLACES),
                stopLimitTimeInForce = TIME_IN_FORCE_GTC,
                symbol = SYMBOL
            )
            sl = 0 if order['orderReports'][0]['type'] == ORDER_TYPE_STOP_LOSS_LIMIT else 1
            li = 1 - sl
            self.stoploss_is_oco = True
            self.sell_order_active = True
            self.sell_order_id = order['orderReports'][li]['orderId']
            self.sell_order_price = order['orderReports'][li]['price']
            self.sell_order_status = order['orderReports'][li]['status']
            self.sell_order_original_qty = order['orderReports'][li]['origQty']
            self.sell_order_executed_qty = order['orderReports'][li]['executedQty']
            self.sell_order_cum_quote_qty = order['orderReports'][li]['cummulativeQuoteQty']
            self.stoploss_order_active = True
            self.stoploss_order_id = order['orderReports'][sl]['orderId']
            self.stoploss_order_price = order['orderReports'][sl]['price']
            self.stoploss_order_status = order['orderReports'][sl]['status']
            self.stoploss_order_original_qty = order['orderReports'][sl]['origQty']
            self.stoploss_order_executed_qty = order['orderReports'][sl]['executedQty']
            self.stoploss_order_cum_quote_qty = order['orderReports'][sl]['cummulativeQuoteQty']
            tg.notify_order_placed(OCO_SELL_TYPE, quantity, price, alert=alert)
            return order['orderReports'][li]['status']
        except Exception as e:
            error_code = _extract_api_error_code(e)
            if not (
                (
                    error_code == ERROR_CODE_INVALID_MESSAGE and
                    str(e).endswith(ERROR_MESSAGE_INVALID_QUANTITY)
                ) or (
                    error_code == ERROR_CODE_NEW_ORDER_REJECTED and
                    str(e).endswith(ERROR_MESSAGE_OCO_PRICES_INCORRECT)
                )
            ):
                tg_msg = 'Warning: exception during attempt to place an OCO sell order\n' \
                    + '{:s}: {:s}'.format(type(e).__name__, str(e))
                tg.notify(tg_msg)
            return error_code
    
    def place_stoploss_order(self, quantity:float, price:float, alert:bool=True) -> Union[str, int, None]:
        try:
            order = self.client.create_order(
                side = SIDE_SELL,
                type = ORDER_TYPE_STOP_LOSS_LIMIT,
                quantity = rounddown(quantity, QTY_DEC_PLACES),
                price = '{:.{:d}f}'.format(price, PRICE_DEC_PLACES),
                stopPrice = '{:.{:d}f}'.format(price, PRICE_DEC_PLACES),
                timeInForce = TIME_IN_FORCE_GTC,
                symbol = SYMBOL
            )
            self.stoploss_is_oco = False
            self.stoploss_order_active = True
            self.stoploss_order_id = order['orderId']
            # values not present in server response, so they have to be faked initially
            self.stoploss_order_price = '{:.8f}'.format(round(price, PRICE_DEC_PLACES))
            self.stoploss_order_status = ORDER_STATUS_NEW
            self.stoploss_order_original_qty = '{:.8f}'.format(rounddown(quantity, QTY_DEC_PLACES))
            self.stoploss_order_executed_qty = '{:.8f}'.format(0.0)
            self.stoploss_order_cum_quote_qty = '{:.8f}'.format(0.0)
            tg.notify_order_placed(STOPLOSS_TYPE, quantity, price, alert=alert)
            return ORDER_STATUS_NEW
        except Exception as e:
            error_code = _extract_api_error_code(e)
            if not (
                error_code == ERROR_CODE_INVALID_MESSAGE and
                str(e).endswith(ERROR_MESSAGE_INVALID_QUANTITY)
            ):
                tg_msg = 'Warning: exception during attempt to place a stop-loss order\n' \
                    + '{:s}: {:s}'.format(type(e).__name__, str(e))
                tg.notify(tg_msg)
            return error_code
    
    def cancel_buy_order(self, alert:bool=True) -> Union[str, int, None]:
        try:
            order = self.client.cancel_order(orderId=self.buy_order_id, symbol=SYMBOL)
            self.buy_order_active = False
            self.buy_order_status = order['status']
            self.buy_order_original_qty = order['origQty']
            self.buy_order_executed_qty = order['executedQty']
            tg.notify_order_cancelled(BUY_TYPE, alert=alert)
            return order['status']
        except Exception as e:
            tg_msg = 'Warning: exception during attempt to cancel buy order #{:s}\n'.format(
                    str(self.buy_order_id)
                ) \
                + '{:s}: {:s}'.format(type(e).__name__, str(e))
            tg.notify(tg_msg)
            return _extract_api_error_code(e)
    
    def cancel_sell_order(self, alert:bool=True) -> Union[str, int, None]:
        try:
            if self.stoploss_is_oco and self.stoploss_order_active:
                return self.cancel_oco_sell_order(OCO_SELL_TYPE, alert=alert)
            else:
                order = self.client.cancel_order(orderId=self.sell_order_id, symbol=SYMBOL)
                self.sell_order_active = False
                self.sell_order_status = order['status']
                self.sell_order_original_qty = order['origQty']
                self.sell_order_executed_qty = order['executedQty']
                tg.notify_order_cancelled(SELL_TYPE, alert=alert)
                return order['status']
        except Exception as e:
            tg_msg = 'Warning: exception during attempt to cancel sell order #{:s}\n'.format(
                    str(self.sell_order_id)
                ) \
                + '{:s}: {:s}'.format(type(e).__name__, str(e))
            tg.notify(tg_msg)
            return _extract_api_error_code(e)
    
    def cancel_oco_sell_order(self, order_type:str, alert:bool=True) -> Union[str, int, None]:
        try:
            order = self.client.cancel_order(
                orderId = self.get_order_id(order_type),
                symbol = SYMBOL
            )
            sl = 0 if order['orderReports'][0]['type'] == ORDER_TYPE_STOP_LOSS_LIMIT else 1
            li = 1 - sl
            self.sell_order_active = False
            self.sell_order_status = order['orderReports'][li]['status']
            self.sell_order_original_qty = order['orderReports'][li]['origQty']
            self.sell_order_executed_qty = order['orderReports'][li]['executedQty']
            self.stoploss_order_active = False
            self.stoploss_order_status = order['orderReports'][sl]['status']
            self.stoploss_order_original_qty = order['orderReports'][sl]['origQty']
            self.stoploss_order_executed_qty = order['orderReports'][sl]['executedQty']
            tg.notify_order_cancelled(order_type, alert=alert)
            if order_type == OCO_STOPLOSS_TYPE:
                return_status = order['orderReports'][sl]['status']
            else:
                return_status = order['orderReports'][li]['status']
            return return_status
        except Exception as e:
            tg_msg = 'Warning: exception during attempt to cancel {:s} order #{:s}\n'.format(
                    ORDER_TYPE_DICT[order_type],
                    str(self.get_order_id(order_type))
                ) \
                + '{:s}: {:s}'.format(type(e).__name__, str(e))
            tg.notify(tg_msg)
            return _extract_api_error_code(e)
    
    def cancel_stoploss_order(self, alert:bool=True) -> Union[str, int, None]:
        try:
            if self.stoploss_is_oco and self.sell_order_active:
                return self.cancel_oco_sell_order(OCO_STOPLOSS_TYPE, alert=alert)
            else:
                order = self.client.cancel_order(orderId=self.stoploss_order_id, symbol=SYMBOL)
                self.stoploss_order_active = False
                self.stoploss_order_status = order['status']
                self.stoploss_order_original_qty = order['origQty']
                self.stoploss_order_executed_qty = order['executedQty']
                tg.notify_order_cancelled(STOPLOSS_TYPE, alert=alert)
                return order['status']
        except Exception as e:
            tg_msg = 'Warning: exception during attempt to cancel stop-loss order #{:s}\n'.format(
                    str(self.stoploss_order_id)
                ) \
                + '{:s}: {:s}'.format(type(e).__name__, str(e))
            tg.notify(tg_msg)
            return _extract_api_error_code(e)
    
    def check_buy_order(self) -> Union[str, int, None]:
        """
        IMPLICIT: state['buy_order_active'] == True
        """
        try:
            order = self.client.get_order(orderId=self.buy_order_id, symbol=SYMBOL)
            self.buy_order_status = order['status']
            self.buy_order_original_qty = order['origQty']
            self.buy_order_executed_qty = order['executedQty']
            self.buy_order_cum_quote_qty = order['cummulativeQuoteQty']
            if (
                order['status'] == ORDER_STATUS_PARTIALLY_FILLED or
                order['status'] == ORDER_STATUS_FILLED
            ):
                self.position_open = True
                if order['status'] == ORDER_STATUS_FILLED:
                    self.position_full = True
            if (
                order['status'] == ORDER_STATUS_FILLED or
                order['status'] == ORDER_STATUS_CANCELED or
                order['status'] == ORDER_STATUS_REJECTED or
                order['status'] == ORDER_STATUS_EXPIRED
            ):
                self.buy_order_active = False
            return order['status']
        except Exception as e:
            print(get_timestamp(), '{:s}: {:s}'.format(type(e).__name__, str(e)), flush=True)
            return _extract_api_error_code(e)
    
    def check_sell_order(self) -> Union[str, int, None]:
        """
        IMPLICIT: state['sell_order_active'] == True
        """
        try:
            order = self.client.get_order(orderId=self.sell_order_id, symbol=SYMBOL)
            self.sell_order_status = order['status']
            self.sell_order_original_qty = order['origQty']
            self.sell_order_executed_qty = order['executedQty']
            self.sell_order_cum_quote_qty = order['cummulativeQuoteQty']
            if (
                order['status'] == ORDER_STATUS_PARTIALLY_FILLED or
                order['status'] == ORDER_STATUS_FILLED
            ):
                self.position_full = False
                if order['status'] == ORDER_STATUS_FILLED:
                    self.position_open = False
            if (
                order['status'] == ORDER_STATUS_FILLED or
                order['status'] == ORDER_STATUS_CANCELED or
                order['status'] == ORDER_STATUS_REJECTED or
                order['status'] == ORDER_STATUS_EXPIRED
            ):
                self.sell_order_active = False
            if self.stoploss_is_oco:
                if (
                    order['status'] == ORDER_STATUS_PARTIALLY_FILLED or
                    order['status'] == ORDER_STATUS_FILLED or
                    order['status'] == ORDER_STATUS_REJECTED or
                    order['status'] == ORDER_STATUS_EXPIRED
                ):
                    self.stoploss_order_active = False
            return order['status']
        except Exception as e:
            print(get_timestamp(), '{:s}: {:s}'.format(type(e).__name__, str(e)), flush=True)
            return _extract_api_error_code(e)
    
    def check_stoploss_order(self) -> Union[str, int, None]:
        """
        IMPLICIT: state['stoploss_order_active'] == True
        """
        try:
            order = self.client.get_order(orderId=self.stoploss_order_id, symbol=SYMBOL)
            self.stoploss_order_status = order['status']
            self.stoploss_order_original_qty = order['origQty']
            self.stoploss_order_executed_qty = order['executedQty']
            self.stoploss_order_cum_quote_qty = order['cummulativeQuoteQty']
            if (
                order['status'] == ORDER_STATUS_PARTIALLY_FILLED or
                order['status'] == ORDER_STATUS_FILLED
            ):
                self.position_full = False
                if order['status'] == ORDER_STATUS_FILLED:
                    self.position_open = False
            if (
                order['status'] == ORDER_STATUS_FILLED or
                order['status'] == ORDER_STATUS_CANCELED or
                order['status'] == ORDER_STATUS_REJECTED or
                order['status'] == ORDER_STATUS_EXPIRED
            ):
                self.stoploss_order_active = False
            if self.stoploss_is_oco:
                if (
                    order['status'] == ORDER_STATUS_PARTIALLY_FILLED or
                    order['status'] == ORDER_STATUS_FILLED or
                    order['status'] == ORDER_STATUS_REJECTED or
                    order['status'] == ORDER_STATUS_EXPIRED
                ):
                    self.sell_order_active = False
            return order['status']
        except Exception as e:
            print(get_timestamp(), '{:s}: {:s}'.format(type(e).__name__, str(e)), flush=True)
            return _extract_api_error_code(e)
    
    def activate_buy_signal(self, price:float, delta:float) -> None:
        self.buy_signal_flag = True
        self.buy_signal_time = tznow().timestamp()
        self.buy_signal_price = price
        self.buy_price_delta = calculate_price_delta(delta, BUY_DELTA_A, BUY_DELTA_B, BUY_DELTA_C)
        tg.notify_signal_activated(BUY_TYPE)
    
    def deactivate_buy_signal(self) -> None:
        self.buy_signal_flag = False
        tg.notify_signal_deactivated(BUY_TYPE)
    
    def activate_sell_signal(self, price:float, delta:float) -> None:
        self.sell_signal_flag = True
        self.sell_signal_time = tznow().timestamp()
        self.sell_signal_price = price
        self.sell_price_delta = calculate_price_delta(delta, SELL_DELTA_A, SELL_DELTA_B, SELL_DELTA_C)
        tg.notify_signal_activated(SELL_TYPE)
    
    def deactivate_sell_signal(self) -> None:
        self.sell_signal_flag = False
        tg.notify_signal_deactivated(SELL_TYPE)
    
    def process_order_status(
        self,
        order_type: str,
        old_status: str,
        new_status: Union[str, int, None],
        old_exec_qty: float,
        new_exec_qty: float,
        update_balances: bool = False
    ) -> bool:
        if self.stoploss_is_oco:
            if order_type == SELL_TYPE:
                order_type = OCO_SELL_TYPE
            elif order_type == STOPLOSS_TYPE:
                order_type = OCO_STOPLOSS_TYPE
        
        exec_qty_inc = rounddown(new_exec_qty, QTY_DEC_PLACES) > rounddown(old_exec_qty, QTY_DEC_PLACES)
        
        if new_status == ORDER_STATUS_FILLED:
            if old_status != ORDER_STATUS_FILLED:
                tg.notify_order_filled(order_type)
            if update_balances:
                self.update_asset_balance()
                self.update_quote_asset_balance()
        elif new_status == ORDER_STATUS_PARTIALLY_FILLED:
            if exec_qty_inc:
                tg.notify_order_partially_filled(order_type)
                if update_balances:
                    self.update_asset_balance()
                    self.update_quote_asset_balance()
        elif isinstance(new_status, str) and new_status != ORDER_STATUS_NEW:
            tg.notify_unexpected_order_status(order_type, new_status)
        
        return exec_qty_inc
    
    def place_and_process_order(self, order_type:str, quantity:float, price:float) -> bool:
        if order_type == BUY_TYPE:
            return_status = self.place_buy_order(quantity, price)
        elif order_type == SELL_TYPE:
            return_status = self.place_sell_order(quantity, price)
        elif order_type == STOPLOSS_TYPE:
            return_status = self.place_stoploss_order(quantity, self.stoploss_level)
        elif order_type == OCO_SELL_TYPE:
            return_status = self.place_oco_sell_order(quantity, price, self.stoploss_level)
            if return_status == ERROR_CODE_NEW_ORDER_REJECTED:
                return self.place_and_process_order(SELL_TYPE, quantity, price)
        else:
            raise ValueError('Unexpected order type encountered in place_and_process_order')
        
        exec_qty = self.get_executed_quantity(order_type)
        
        self.process_order_status(
            order_type,
            ORDER_STATUS_NEW,
            return_status,
            0.0,
            exec_qty
        )
        
        if return_status == ERROR_CODE_INVALID_MESSAGE:
            self.set_order_timeout()
            success = False
        else:
            success = True
        return success
    
    def check_and_process_order(self, order_type:str, update_balances:bool=False) -> bool:
        old_exec_qty = self.get_executed_quantity(order_type)
        
        if order_type == BUY_TYPE:
            old_status = self.buy_order_status
            new_status = self.check_buy_order()
        elif order_type == SELL_TYPE or order_type == OCO_SELL_TYPE:
            old_status = self.sell_order_status
            new_status = self.check_sell_order()
        elif order_type == STOPLOSS_TYPE or order_type == OCO_STOPLOSS_TYPE:
            old_status = self.stoploss_order_status
            new_status = self.check_stoploss_order()
        else:
            raise ValueError('Unexpected order type encountered in check_and_process_order')
        
        new_exec_qty = self.get_executed_quantity(order_type)
        
        exec_qty_increased = self.process_order_status(
            order_type,
            old_status,
            new_status,
            old_exec_qty,
            new_exec_qty,
            update_balances = update_balances
        )
        
        return exec_qty_increased
    
    def update_stoploss_level(
        self,
        x: float,
        distance: float = 0.0,
        distance_factor: float = 2.0,
        pct_offset: float = 15.0,
        override_condition: str = 'greater'
    ) -> bool:
        new_stoploss_level = round(
            calculate_stoploss(x, distance, distance_factor, pct_offset),
            PRICE_DEC_PLACES
        )
        if override_condition == 'greater':
            condition_met = new_stoploss_level > self.stoploss_level
        elif override_condition == 'not_equal':
            condition_met = new_stoploss_level != self.stoploss_level
        else:
            raise ValueError("Invalid override_condition: must be 'greater' or 'not_equal'")
        if condition_met:
            self.stoploss_level = new_stoploss_level
            tg.notify_stoploss_update(new_stoploss_level)
        return condition_met
    
    @property
    def unsaved_changes(self) -> bool:
        return self.__unsaved_changes
    
    @property
    def asset_balance(self) -> dict:
        return self.__state['asset_balance']
    
    @asset_balance.setter
    def asset_balance(self, asset_balance:dict) -> None:
        self.__state['asset_balance'] = asset_balance
        self.__unsaved_changes = True
    
    @property
    def quote_asset_balance(self) -> dict:
        return self.__state['quote_asset_balance']
    
    @quote_asset_balance.setter
    def quote_asset_balance(self, quote_asset_balance:dict) -> None:
        self.__state['quote_asset_balance'] = quote_asset_balance
        self.__unsaved_changes = True
    
    @property
    def mode(self) -> str:
        return self.__state['mode']
    
    @mode.setter
    def mode(self, mode:str) -> None:
        self.__state['mode'] = mode
        self.__unsaved_changes = True
    
    @property
    def trading_enabled(self) -> bool:
        return self.__state['trading_enabled']
    
    @trading_enabled.setter
    def trading_enabled(self, trading_enabled:bool) -> None:
        self.__state['trading_enabled'] = trading_enabled
        self.__unsaved_changes = True
    
    @property
    def stoploss_enabled(self) -> bool:
        return self.__state['stoploss_enabled']
    
    @stoploss_enabled.setter
    def stoploss_enabled(self, stoploss_enabled:bool) -> None:
        self.__state['stoploss_enabled'] = stoploss_enabled
        self.__unsaved_changes = True
    
    @property
    def stoploss_level(self) -> float:
        return self.__state['stoploss_level']
    
    @stoploss_level.setter
    def stoploss_level(self, stoploss_level:float) -> None:
        self.__state['stoploss_level'] = stoploss_level
        self.__unsaved_changes = True
    
    @property
    def position_open(self) -> bool:
        return self.__state['position_open']
    
    @position_open.setter
    def position_open(self, position_open:bool) -> None:
        self.__state['position_open'] = position_open
        self.__unsaved_changes = True
    
    @property
    def position_full(self) -> bool:
        return self.__state['position_full']
    
    @position_full.setter
    def position_full(self, position_full:bool) -> None:
        self.__state['position_full'] = position_full
        self.__unsaved_changes = True
    
    @property
    def buy_signal_flag(self) -> bool:
        return self.__state['buy_signal_flag']
    
    @buy_signal_flag.setter
    def buy_signal_flag(self, buy_signal_flag:bool) -> None:
        self.__state['buy_signal_flag'] = buy_signal_flag
        self.__unsaved_changes = True
    
    @property
    def buy_signal_time(self) -> float:
        return self.__state['buy_signal_time']
    
    @buy_signal_time.setter
    def buy_signal_time(self, buy_signal_time:float) -> None:
        self.__state['buy_signal_time'] = buy_signal_time
        self.__unsaved_changes = True
    
    @property
    def buy_signal_price(self) -> float:
        return self.__state['buy_signal_price']
    
    @buy_signal_price.setter
    def buy_signal_price(self, buy_signal_price:float) -> None:
        self.__state['buy_signal_price'] = buy_signal_price
        self.__unsaved_changes = True
    
    @property
    def buy_price_delta(self) -> float:
        return self.__state['buy_price_delta']
    
    @buy_price_delta.setter
    def buy_price_delta(self, buy_price_delta:float) -> None:
        self.__state['buy_price_delta'] = buy_price_delta
        self.__unsaved_changes = True
    
    @property
    def buy_order_req_flag(self) -> bool:
        return self.__state['buy_order_req_flag']
    
    @buy_order_req_flag.setter
    def buy_order_req_flag(self, buy_order_req_flag:bool) -> None:
        self.__state['buy_order_req_flag'] = buy_order_req_flag
        self.__unsaved_changes = True
    
    @property
    def buy_target_price(self) -> float:
        return self.__state['buy_target_price']
    
    @buy_target_price.setter
    def buy_target_price(self, buy_target_price:float) -> None:
        self.__state['buy_target_price'] = buy_target_price
        self.__unsaved_changes = True
    
    @property
    def buy_order_active(self) -> bool:
        return self.__state['buy_order_active']
    
    @buy_order_active.setter
    def buy_order_active(self, buy_order_active:bool) -> None:
        self.__state['buy_order_active'] = buy_order_active
        self.__unsaved_changes = True
    
    @property
    def buy_order_id(self) -> int:
        return self.__state['buy_order_id']
    
    @buy_order_id.setter
    def buy_order_id(self, buy_order_id:int) -> None:
        self.__state['buy_order_id'] = buy_order_id
        self.__unsaved_changes = True
    
    @property
    def buy_order_price(self) -> str:
        return self.__state['buy_order_price']
    
    @buy_order_price.setter
    def buy_order_price(self, buy_order_price:str) -> None:
        self.__state['buy_order_price'] = buy_order_price
        self.__unsaved_changes = True
    
    @property
    def buy_order_status(self) -> str:
        return self.__state['buy_order_status']
    
    @buy_order_status.setter
    def buy_order_status(self, buy_order_status:str) -> None:
        self.__state['buy_order_status'] = buy_order_status
        self.__unsaved_changes = True
    
    @property
    def buy_order_original_qty(self) -> str:
        return self.__state['buy_order_original_qty']
    
    @buy_order_original_qty.setter
    def buy_order_original_qty(self, buy_order_original_qty:str) -> None:
        self.__state['buy_order_original_qty'] = buy_order_original_qty
        self.__unsaved_changes = True
    
    @property
    def buy_order_executed_qty(self) -> str:
        return self.__state['buy_order_executed_qty']
    
    @buy_order_executed_qty.setter
    def buy_order_executed_qty(self, buy_order_executed_qty:str) -> None:
        self.__state['buy_order_executed_qty'] = buy_order_executed_qty
        self.__unsaved_changes = True
    
    @property
    def buy_order_cum_quote_qty(self) -> str:
        return self.__state['buy_order_cum_quote_qty']
    
    @buy_order_cum_quote_qty.setter
    def buy_order_cum_quote_qty(self, buy_order_cum_quote_qty:str) -> None:
        self.__state['buy_order_cum_quote_qty'] = buy_order_cum_quote_qty
        self.__unsaved_changes = True
    
    @property
    def sell_signal_flag(self) -> bool:
        return self.__state['sell_signal_flag']
    
    @sell_signal_flag.setter
    def sell_signal_flag(self, sell_signal_flag:bool) -> None:
        self.__state['sell_signal_flag'] = sell_signal_flag
        self.__unsaved_changes = True
    
    @property
    def sell_signal_time(self) -> float:
        return self.__state['sell_signal_time']
    
    @sell_signal_time.setter
    def sell_signal_time(self, sell_signal_time:float) -> None:
        self.__state['sell_signal_time'] = sell_signal_time
        self.__unsaved_changes = True
    
    @property
    def sell_signal_price(self) -> float:
        return self.__state['sell_signal_price']
    
    @sell_signal_price.setter
    def sell_signal_price(self, sell_signal_price:float) -> None:
        self.__state['sell_signal_price'] = sell_signal_price
        self.__unsaved_changes = True
    
    @property
    def sell_price_delta(self) -> float:
        return self.__state['sell_price_delta']
    
    @sell_price_delta.setter
    def sell_price_delta(self, sell_price_delta:float) -> None:
        self.__state['sell_price_delta'] = sell_price_delta
        self.__unsaved_changes = True
    
    @property
    def sell_order_req_flag(self) -> bool:
        return self.__state['sell_order_req_flag']
    
    @sell_order_req_flag.setter
    def sell_order_req_flag(self, sell_order_req_flag:bool) -> None:
        self.__state['sell_order_req_flag'] = sell_order_req_flag
        self.__unsaved_changes = True
    
    @property
    def sell_target_price(self) -> float:
        return self.__state['sell_target_price']
    
    @sell_target_price.setter
    def sell_target_price(self, sell_target_price:float) -> None:
        self.__state['sell_target_price'] = sell_target_price
        self.__unsaved_changes = True
    
    @property
    def sell_order_active(self) -> bool:
        return self.__state['sell_order_active']
    
    @sell_order_active.setter
    def sell_order_active(self, sell_order_active:bool) -> None:
        self.__state['sell_order_active'] = sell_order_active
        self.__unsaved_changes = True
    
    @property
    def sell_order_id(self) -> int:
        return self.__state['sell_order_id']
    
    @sell_order_id.setter
    def sell_order_id(self, sell_order_id:int) -> None:
        self.__state['sell_order_id'] = sell_order_id
        self.__unsaved_changes = True
    
    @property
    def sell_order_price(self) -> str:
        return self.__state['sell_order_price']
    
    @sell_order_price.setter
    def sell_order_price(self, sell_order_price:str) -> None:
        self.__state['sell_order_price'] = sell_order_price
        self.__unsaved_changes = True
    
    @property
    def sell_order_status(self) -> str:
        return self.__state['sell_order_status']
    
    @sell_order_status.setter
    def sell_order_status(self, sell_order_status:str) -> None:
        self.__state['sell_order_status'] = sell_order_status
        self.__unsaved_changes = True
    
    @property
    def sell_order_original_qty(self) -> str:
        return self.__state['sell_order_original_qty']
    
    @sell_order_original_qty.setter
    def sell_order_original_qty(self, sell_order_original_qty:str) -> None:
        self.__state['sell_order_original_qty'] = sell_order_original_qty
        self.__unsaved_changes = True
    
    @property
    def sell_order_executed_qty(self) -> str:
        return self.__state['sell_order_executed_qty']
    
    @sell_order_executed_qty.setter
    def sell_order_executed_qty(self, sell_order_executed_qty:str) -> None:
        self.__state['sell_order_executed_qty'] = sell_order_executed_qty
        self.__unsaved_changes = True
    
    @property
    def sell_order_cum_quote_qty(self) -> str:
        return self.__state['sell_order_cum_quote_qty']
    
    @sell_order_cum_quote_qty.setter
    def sell_order_cum_quote_qty(self, sell_order_cum_quote_qty:str) -> None:
        self.__state['sell_order_cum_quote_qty'] = sell_order_cum_quote_qty
        self.__unsaved_changes = True
    
    @property
    def stoploss_order_req_flag(self) -> bool:
        return self.__state['stoploss_order_req_flag']
    
    @stoploss_order_req_flag.setter
    def stoploss_order_req_flag(self, stoploss_order_req_flag:bool) -> None:
        self.__state['stoploss_order_req_flag'] = stoploss_order_req_flag
        self.__unsaved_changes = True
    
    @property
    def stoploss_order_active(self) -> bool:
        return self.__state['stoploss_order_active']
    
    @stoploss_order_active.setter
    def stoploss_order_active(self, stoploss_order_active:bool) -> None:
        self.__state['stoploss_order_active'] = stoploss_order_active
        self.__unsaved_changes = True
    
    @property
    def stoploss_order_id(self) -> int:
        return self.__state['stoploss_order_id']
    
    @stoploss_order_id.setter
    def stoploss_order_id(self, stoploss_order_id:int) -> None:
        self.__state['stoploss_order_id'] = stoploss_order_id
        self.__unsaved_changes = True
    
    @property
    def stoploss_order_price(self) -> str:
        return self.__state['stoploss_order_price']
    
    @stoploss_order_price.setter
    def stoploss_order_price(self, stoploss_order_price:str) -> None:
        self.__state['stoploss_order_price'] = stoploss_order_price
        self.__unsaved_changes = True
    
    @property
    def stoploss_order_status(self) -> str:
        return self.__state['stoploss_order_status']
    
    @stoploss_order_status.setter
    def stoploss_order_status(self, stoploss_order_status:str) -> None:
        self.__state['stoploss_order_status'] = stoploss_order_status
        self.__unsaved_changes = True
    
    @property
    def stoploss_order_original_qty(self) -> str:
        return self.__state['stoploss_order_original_qty']
    
    @stoploss_order_original_qty.setter
    def stoploss_order_original_qty(self, stoploss_order_original_qty:str) -> None:
        self.__state['stoploss_order_original_qty'] = stoploss_order_original_qty
        self.__unsaved_changes = True
    
    @property
    def stoploss_order_executed_qty(self) -> str:
        return self.__state['stoploss_order_executed_qty']
    
    @stoploss_order_executed_qty.setter
    def stoploss_order_executed_qty(self, stoploss_order_executed_qty:str) -> None:
        self.__state['stoploss_order_executed_qty'] = stoploss_order_executed_qty
        self.__unsaved_changes = True
    
    @property
    def stoploss_order_cum_quote_qty(self) -> str:
        return self.__state['stoploss_order_cum_quote_qty']
    
    @stoploss_order_cum_quote_qty.setter
    def stoploss_order_cum_quote_qty(self, stoploss_order_cum_quote_qty:str) -> None:
        self.__state['stoploss_order_cum_quote_qty'] = stoploss_order_cum_quote_qty
        self.__unsaved_changes = True
    
    @property
    def stoploss_is_oco(self) -> bool:
        return self.__state['stoploss_is_oco']
    
    @stoploss_is_oco.setter
    def stoploss_is_oco(self, stoploss_is_oco:bool) -> None:
        self.__state['stoploss_is_oco'] = stoploss_is_oco
        self.__unsaved_changes = True
    
    @property
    def order_timeout(self) -> float:
        return self.__state['order_timeout']
    
    @order_timeout.setter
    def order_timeout(self, order_timeout:float) -> None:
        self.__state['order_timeout'] = order_timeout
        self.__unsaved_changes = True
    
    @property
    def stoploss_hit_timeout(self) -> dt.datetime:
        return self.__state['stoploss_hit_timeout']
    
    @stoploss_hit_timeout.setter
    def stoploss_hit_timeout(self, stoploss_hit_timeout:dt.datetime) -> None:
        self.__state['stoploss_hit_timeout'] = stoploss_hit_timeout
        self.__unsaved_changes = True

try:
    tsm = TradeStateMachine()
except Exception as e:
    print_exception_and_shutdown(e)
