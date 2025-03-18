import datetime as dt
import numpy as np
from typing import List, Dict

from pydantic import BaseModel, Field

from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.common.instruments import Instrument, Quantity
from nautilus_trader.common.timeframes import Timeframe
from nautilus_trader.common.periods import Period
from nautilus_trader.model.data import Bar
from nautilus_trader.trading.execution_commands import MarketOrderCommand
from nautilus_trader.common.enums import OrderSide
from nautilus_trader.trading.position import Position

class VolTargetConfig(BaseModel):
    target_volatility: float = Field(..., description="Target volatility for the portfolio (e.g., 0.10 for 10%)")
    lookback_short: int = Field(20, description="Lookback period for short-term volatility calculation")
    lookback_long: int = Field(100, description="Lookback period for long-term volatility calculation")
    vol_annu: int = Field(252, description="Number of trading days in a year for annualization")
    risky_asset: List[str] = Field(..., description="List of risky asset tickers")
    start_date: str = Field(..., description="Start date for the backtest (YYYY-MM-DD)")


class VolTargetBacktestStrategy(Strategy):
    config: VolTargetConfig
    instruments: Dict[str, Instrument] = {}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = VolTargetConfig(**self.config)
        self.instruments = {
            ticker: self.engine.get_instrument(ticker) for ticker in self.config.risky_asset
        }
        if not all(self.instruments.values()):
            raise ValueError(f"Could not find all specified assets: {self.config.risky_asset}")

    def get_historical_prices(self, instrument: Instrument, date: dt.date, window: int) -> Optional[pd.DataFrame]:
        start_date = self.calendar.offset(date, -window)
        history = self.engine.get_historical_data(
            instrument=instrument,
            timeframe=Timeframe.DAILY,
            period=Period.range(start_date, date)
        )
        if not history:
            return None
        df = pd.DataFrame([{'ts': bar.ts, 'close': bar.close} for bar in history])
        df['ts'] = pd.to_datetime(df['ts']).dt.date
        df.set_index('ts', inplace=True)
        return df

    def calc_return(self, instrument: Instrument, t: dt.date) -> Optional[float]:
        current_bar = self.engine.get_last_bar(instrument, Timeframe.DAILY, t)
        previous_bar = self.engine.get_last_bar(instrument, Timeframe.DAILY, self.calendar.offset(t, -1))
        if current_bar and previous_bar:
            asset_level_t = current_bar.close
            asset_level_tm1 = previous_bar.close
            if asset_level_tm1 != 0:
                return (asset_level_t / asset_level_tm1) - 1
        return None

    def calc_volatility(self, instrument: Instrument, window: int, t: dt.date) -> Optional[float]:
        returns =
        for i in range(window):
            lookback_date = self.calendar.offset(t, -i)
            ret = self.calc_return(instrument, lookback_date)
            if ret is not None:
                returns.append(ret)

        if returns:
            returns_mean = np.mean(returns)
            vol_sum = np.sum([(r - returns_mean) ** 2 for r in returns])
            return np.sqrt(self.config.vol_annu * vol_sum / len(returns))
        return None

    def calc_underlying_real_vol(self, instrument: Instrument, t: dt.date) -> float:
        vol_underlying_short = self.calc_volatility(instrument, self.config.lookback_short, t)
        vol_underlying_long = self.calc_volatility(instrument, self.config.lookback_long, t)

        if vol_underlying_short is not None and vol_underlying_long is not None:
            return max(vol_underlying_short, vol_underlying_long)
        elif vol_underlying_short is not None:
            return vol_underlying_short
        elif vol_underlying_long is not None:
            return vol_underlying_long
        return 0.0

    def calc_portfolio_risk(self, t: dt.date) -> float:
        portfolio_risk =
        for ticker, instrument in self.instruments.items():
            underlying_real_vol = self.calc_underlying_real_vol(instrument, t)
            portfolio_risk.append(underlying_real_vol)

        return max(portfolio_risk) if portfolio_risk else 0.0

    def calc_weights(self, instrument: Instrument, t: dt.date) -> float:
        portfolio_risk = self.calc_portfolio_risk(t)
        underlying_real_vol = self.calc_underlying_real_vol(instrument, t)
        if portfolio_risk > 0:
            weight = (underlying_real_vol / portfolio_risk) * (self.config.target_volatility / portfolio_risk)
        else:
            weight = 1 / len(self.config.risky_asset) # Equally weight if portfolio volatility is zero

        return weight

    def calc_normalized_weights(self, t: dt.date) -> Dict[str, float]:
        total_weight = 0
        weights = {}
        for ticker, instrument in self.instruments.items():
            weight = self.calc_weights(instrument, t)
            weights[ticker] = weight
            total_weight += weight

        normalized_weights = {}
        if total_weight > 0:
            for ticker, weight in weights.items():
                normalized_weights[ticker] = weight / total_weight
        else:
            # Equally weight if total weight is zero
            for ticker in self.instruments:
                normalized_weights[ticker] = 1 / len(self.instruments)

        return normalized_weights

    def rebalance(self, t: dt.date, target_weights: Dict[str, float]):
        for ticker, instrument in self.instruments.items():
            target_weight = target_weights.get(ticker, 0.0)
            current_position = self.engine.get_position(instrument)

            # Basic position sizing: Assume a fixed portfolio value for simplicity
            portfolio_value = 100000.0  # Example portfolio value
            target_value = portfolio_value * target_weight

            if current_position:
                current_value = current_position.market_value
                value_to_trade = target_value - current_value

                if instrument.lot_size > 0 and instrument.tick_size > 0:
                    price = self.engine.get_last_bar(instrument, Timeframe.DAILY, t).close if self.engine.get_last_bar(instrument, Timeframe.DAILY, t) else None
                    if price is not None:
                        quantity_to_trade = value_to_trade / price

                        if quantity_to_trade > 1e-6: # Avoid very small trades
                            order_side = OrderSide.BUY if quantity_to_trade > 0 else OrderSide.SELL
                            quantity = Quantity.from_int(abs(int(quantity_to_trade / instrument.lot_size) * instrument.lot_size)) # Trade in lot sizes
                            if quantity.amount > 0:
                                self.submit_order(MarketOrderCommand(instrument, order_side, quantity))
            elif target_weight > 0:
                price = self.engine.get_last_bar(instrument, Timeframe.DAILY, t).close if self.engine.get_last_bar(instrument, Timeframe.DAILY, t) else None
                if price is not None and instrument.lot_size > 0:
                    quantity_to_trade = target_value / price
                    order_side = OrderSide.BUY if quantity_to_trade > 0 else OrderSide.SELL
                    quantity = Quantity.from_int(abs(int(quantity_to_trade / instrument.lot_size) * instrument.lot_size))
                    if quantity.amount > 0:
                        self.submit_order(MarketOrderCommand(instrument, order_side, quantity))

    def on_bar(self, bar: Bar):
        trade_date = bar.trade_date.date()
        target_weights = self.calc_normalized_weights(trade_date)

        self.logger.info(f"Target Weights for {trade_date}: {target_weights}")

        self.rebalance(trade_date, target_weights)

        for ticker, instrument in self.instruments.items():
            position = self.engine.get_position(instrument)
            if position:
                self.logger.info(f"Open Position - {ticker}: Size={position.net_quantity}, Average Price={position.average_price}")

    def on_start(self):
        self.logger.info("Volatility Target Strategy Started")
        if not self.config.risky_asset:
            self.logger.error("No risky assets specified in the configuration.")

    def on_stop(self):
        self.logger.info("Volatility Target Strategy Stopped")
