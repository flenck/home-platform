"""TDS sensor entity for water quality monitoring."""

import logging

from homeplatform.components.sensor import SensorEntity
from homeplatform.const import STATE_OK, STATE_PROBLEM

_LOGGER = logging.getLogger(__name__)

# TDS classification thresholds (ppm)
TDS_PURE = 10       # 纯净水
TDS_MINERAL = 90    # 山泉水/矿化水
TDS_CLEAN = 260     # 净化水
TDS_TAP = 600       # 自来水 (above this is polluted)


class TDSSensor(SensorEntity):
    """TDS (Total Dissolved Solids) sensor entity."""

    def __init__(self) -> None:
        super().__init__(metric="tds", name="TDS 水质", unit="ppm")

    def status_label(self) -> str:
        if self._value is None:
            return "无数据"
        if self._value < TDS_PURE:
            return "纯净水"
        if self._value < TDS_MINERAL:
            return "山泉水/矿化水"
        if self._value < TDS_CLEAN:
            return "净化水"
        if self._value < TDS_TAP:
            return "自来水"
        return "污染水"

    def status_class(self) -> str:
        if self._value is None:
            return "unknown"
        if self._value < TDS_CLEAN:
            return "good"
        if self._value < TDS_TAP:
            return "warn"
        return "bad"
