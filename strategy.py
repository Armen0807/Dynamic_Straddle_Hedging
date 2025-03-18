import datetime as dt
import numpy as np
from loguru import logger

from grt_lib_price_loader.price_loader import AbstractPriceLoader
from grt_lib_backtest.backtest_strat import AbstractBacktestStrategy


class VolTargetBacktestStrategy(AbstractBacktestStrategy):
	class Config:
		target_volatility: float
		lookback_short: int
		lookback_long: int
		vol_annu: int
		risky_asset: list[str]
		start_date: str

	config: Config

	def __init__(self, price_loader: AbstractPriceLoader):
		self.price_loader = price_loader

	def calc_return(self, underlying: str, t: dt.date) -> float:
		tm1 = self.price_loader.busday_add(t, -1)
		asset_level_t = self.price_loader.get_price(underlying, t)
		asset_level_tm1 = self.price_loader.get_price(underlying, tm1)

		asset_return = (asset_level_t / asset_level_tm1) - 1
		return asset_return
	def calc_volatility(self, underlying: str, window: int, t: dt.date) -> float:
		returns = []
		for i in range(window):
			tmi = self.price_loader.busday_add(t, -i)
			returns.append(self.calc_return(underlying, tmi))

		returns_mean = np.mean(returns)

		vol_sum = 0
		for i in range(window):
			vol_sum += (returns[i] - returns_mean) ** 2

		return np.square(self.config.vol_annu * vol_sum / window)

	def calc_underlying_real_vol(self, underlying: str, t: dt.date) -> float:
		vol_underlying_short = self.calc_volatility(underlying, self.config.lookback_short, t)
		vol_underlying_long = self.calc_volatility(underlying, self.config.lookback_long, t)

		return max(vol_underlying_short, vol_underlying_long)

	def calc_portfolio_risk(self, t: dt.date) -> float:
		portfolio_risk = []
		for underlying in self.config.risky_asset:
			underlying_real_vol = self.calc_underlying_real_vol(underlying, t)
			portfolio_risk.append(underlying_real_vol)

		return max(portfolio_risk)

	def calc_weights(self, underlying: str, t: dt.date) -> float:
		portfolio_risk = self.calc_portfolio_risk(t)
		underlying_real_vol = self.calc_underlying_real_vol(underlying, t)
		if portfolio_risk > 0:
			weight = (underlying_real_vol / portfolio_risk) * (self.config.target_volatility / portfolio_risk)
		else:
			weight = 1 / len(self.config.risky_asset)  # Equally weight if portfolio volatility is zero

		return weight

	def calc_normalized_weights(self, underlying: str, t: dt.date) -> float:
		total_weight = 0
		for _underlying in self.config.risky_asset:
			weight = self.calc_weights(_underlying, t)
			total_weight += weight

		underlying_weight = self.calc_weights(underlying, t)
		normalized_weight = underlying_weight / total_weight if total_weight > 0 else 0
		return normalized_weight

	def rebalance(self, underlying: str, t: dt.date):
		weight = self.calc_normalized_weights(underlying, t)
		self.order_target_percent(underlying, target=weight)

	def execute_strategy(self, t: dt.date):
		for underlying in self.config.risky_asset:
			weight = self.calc_normalized_weights(underlying, t)
			logger.info(f"Weights - {underlying}: {weight}")

			self.rebalance(underlying, t)

			position = self.getposition(data=self.getdatabyname(underlying))
			logger.info(f"Open Position - {underlying}: Size={position.size}, Price={position.price}")
