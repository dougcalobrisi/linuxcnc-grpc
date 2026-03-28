"""
HAL Python API data to protobuf mapper.

Maps hal.get_info_*() dictionaries to HalSystemStatus protobuf messages.

Note: The HAL Python API returns dicts with UPPERCASE keys:
- Pins: NAME, VALUE, DIRECTION, TYPE
- Signals: NAME, VALUE, DRIVER, TYPE
- Params: NAME, DIRECTION, VALUE (no TYPE field - infer from value)
"""

import time
from typing import List, Dict, Any, Optional

from linuxcnc_pb import hal_pb2


class HalMapper:
    """Maps HAL introspection data to protobuf messages."""

    # HAL type constants (from hal module, with fallback values)
    HAL_BIT = 1
    HAL_FLOAT = 2
    HAL_S32 = 3
    HAL_U32 = 4
    HAL_S64 = 5
    HAL_U64 = 6
    HAL_PORT = 7

    # HAL pin direction constants
    HAL_IN = 16
    HAL_OUT = 32
    HAL_IO = 48

    # HAL param direction constants
    HAL_RO = 64
    HAL_RW = 192

    def __init__(
        self,
        pins: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
        params: List[Dict[str, Any]]
    ):
        """
        Initialize mapper with HAL introspection data.

        Args:
            pins: Result from hal.get_info_pins()
            signals: Result from hal.get_info_signals()
            params: Result from hal.get_info_params()
        """
        self._pins = pins
        self._signals = signals
        self._params = params
        self._signal_pins = self._build_signal_pin_map()
        self._components = self._derive_components()

    @staticmethod
    def _get(d: Dict[str, Any], key: str, default: Any = None) -> Any:
        """Get value from dict, trying UPPERCASE first (HAL API convention), then lowercase."""
        return d.get(key.upper(), d.get(key.lower(), d.get(key, default)))

    def _build_signal_pin_map(self) -> Dict[str, Dict[str, Any]]:
        """Build mapping from signal names to connected pins."""
        signal_map: Dict[str, Dict[str, Any]] = {}

        # Initialize all signals with their driver from the signal info
        for sig in self._signals:
            sig_name = self._get(sig, 'name', '')
            driver = self._get(sig, 'driver', '')
            signal_map[sig_name] = {
                'driver': driver,
                'readers': []
            }

        # Note: HAL doesn't provide signal connection info in pin dicts,
        # so we can't determine readers from the pin data directly.
        # The driver comes from the signal info itself.

        return signal_map

    def _derive_components(self) -> Dict[str, Dict[str, Any]]:
        """Derive component information from pins and params."""
        components: Dict[str, Dict[str, Any]] = {}

        # Extract components from pins
        for pin in self._pins:
            name = self._get(pin, 'name', '')
            # Component name is everything before the last dot
            if '.' in name:
                comp_name = name.rsplit('.', 1)[0]
            else:
                comp_name = name
            owner_id = self._get(pin, 'owner', 0)

            if comp_name not in components:
                components[comp_name] = {
                    'name': comp_name,
                    'id': owner_id,
                    'pins': [],
                    'params': [],
                    'ready': True  # Assume ready if has pins
                }
            components[comp_name]['pins'].append(name)

        # Add params to their components
        for param in self._params:
            name = self._get(param, 'name', '')
            if '.' in name:
                comp_name = name.rsplit('.', 1)[0]
            else:
                comp_name = name

            if comp_name in components:
                components[comp_name]['params'].append(name)
            else:
                # Component only has params, no pins
                owner_id = self._get(param, 'owner', 0)
                components[comp_name] = {
                    'name': comp_name,
                    'id': owner_id,
                    'pins': [],
                    'params': [name],
                    'ready': True
                }

        return components

    def map_to_proto(self) -> hal_pb2.HalSystemStatus:
        """Map all HAL data to a complete HalSystemStatus message."""
        return hal_pb2.HalSystemStatus(
            timestamp=int(time.time() * 1e9),
            pins=[self.map_pin(p) for p in self._pins],
            signals=[self.map_signal(s) for s in self._signals],
            params=[self.map_param(p) for p in self._params],
            components=[self.map_component(c) for c in self._components.values()],
            message_level=hal_pb2.MSG_INFO,
            is_sim=False,
            is_rt=True,
            is_userspace=False,
            kernel_version=""
        )

    def map_pin(self, pin_info: Dict[str, Any]) -> hal_pb2.HalPinInfo:
        """Map a single pin info dict to HalPinInfo proto."""
        name = self._get(pin_info, 'name', '')
        short_name = name.rsplit('.', 1)[-1] if '.' in name else name
        component = name.rsplit('.', 1)[0] if '.' in name else ''
        hal_type = self._get(pin_info, 'type', self.HAL_FLOAT)
        direction = self._get(pin_info, 'direction', self.HAL_IN)
        value = self._get(pin_info, 'value', 0)
        signal = self._get(pin_info, 'signal', '')

        return hal_pb2.HalPinInfo(
            name=name,
            short_name=short_name,
            component=component,
            type=self._map_hal_type(hal_type),
            direction=self._map_pin_direction(direction),
            value=self.map_value(value, hal_type),
            signal=signal if signal else '',
            has_writer=(direction == self.HAL_OUT)
        )

    def map_signal(self, signal_info: Dict[str, Any]) -> hal_pb2.HalSignalInfo:
        """Map a single signal info dict to HalSignalInfo proto."""
        name = self._get(signal_info, 'name', '')
        hal_type = self._get(signal_info, 'type', self.HAL_FLOAT)
        value = self._get(signal_info, 'value', 0)
        driver = self._get(signal_info, 'driver', '')

        # Get connected pins from our map
        # Note: readers list is always empty because the HAL API doesn't
        # expose signal-to-reader-pin connections. reader_count will be 0.
        conn = self._signal_pins.get(name, {'driver': driver, 'readers': []})

        return hal_pb2.HalSignalInfo(
            name=name,
            type=self._map_hal_type(hal_type),
            value=self.map_value(value, hal_type),
            driver=conn['driver'],
            readers=conn['readers'],
            reader_count=len(conn['readers'])
        )

    def map_param(self, param_info: Dict[str, Any]) -> hal_pb2.HalParamInfo:
        """Map a single param info dict to HalParamInfo proto."""
        name = self._get(param_info, 'name', '')
        short_name = name.rsplit('.', 1)[-1] if '.' in name else name
        component = name.rsplit('.', 1)[0] if '.' in name else ''
        direction = self._get(param_info, 'direction', self.HAL_RW)
        value = self._get(param_info, 'value', 0)

        # HAL params don't have a TYPE field - infer from value
        hal_type = self._infer_type_from_value(value)

        return hal_pb2.HalParamInfo(
            name=name,
            short_name=short_name,
            component=component,
            type=self._map_hal_type(hal_type),
            direction=self._map_param_direction(direction),
            value=self.map_value(value, hal_type)
        )

    def _infer_type_from_value(self, value: Any) -> int:
        """Infer HAL type from a Python value."""
        if isinstance(value, bool):
            return self.HAL_BIT
        elif isinstance(value, float):
            return self.HAL_FLOAT
        elif isinstance(value, int):
            # Could be s32, u32, s64, u64 - default to s32 for simplicity
            if value < 0:
                return self.HAL_S32
            elif value > 0xFFFFFFFF:
                return self.HAL_U64
            else:
                return self.HAL_S32
        else:
            return self.HAL_FLOAT

    def map_component(self, comp_info: Dict[str, Any]) -> hal_pb2.HalComponentInfo:
        """Map component info dict to HalComponentInfo proto."""
        return hal_pb2.HalComponentInfo(
            name=comp_info.get('name', ''),
            id=comp_info.get('id', 0),
            ready=comp_info.get('ready', True),
            type=comp_info.get('type', 1),  # 1 = user component
            pid=comp_info.get('pid', 0),
            pins=comp_info.get('pins', []),
            params=comp_info.get('params', [])
        )

    def map_value(self, value: Any, hal_type: int) -> hal_pb2.HalValue:
        """Map a raw HAL value to HalValue proto based on type."""
        if hal_type == self.HAL_BIT:
            return hal_pb2.HalValue(bit_value=bool(value))
        elif hal_type == self.HAL_FLOAT:
            return hal_pb2.HalValue(float_value=float(value) if value is not None else 0.0)
        elif hal_type == self.HAL_S32:
            return hal_pb2.HalValue(s32_value=int(value) if value is not None else 0)
        elif hal_type == self.HAL_U32:
            val = int(value) if value is not None else 0
            return hal_pb2.HalValue(u32_value=val & 0xFFFFFFFF)
        elif hal_type == self.HAL_S64:
            return hal_pb2.HalValue(s64_value=int(value) if value is not None else 0)
        elif hal_type == self.HAL_U64:
            val = int(value) if value is not None else 0
            return hal_pb2.HalValue(u64_value=val & 0xFFFFFFFFFFFFFFFF)
        else:
            # Default to float for unknown types
            return hal_pb2.HalValue(float_value=float(value) if value is not None else 0.0)

    def _map_hal_type(self, hal_type: int) -> int:
        """Map HAL type constant to proto HalType enum."""
        mapping = {
            self.HAL_BIT: hal_pb2.HAL_BIT,
            self.HAL_FLOAT: hal_pb2.HAL_FLOAT,
            self.HAL_S32: hal_pb2.HAL_S32,
            self.HAL_U32: hal_pb2.HAL_U32,
            self.HAL_S64: hal_pb2.HAL_S64,
            self.HAL_U64: hal_pb2.HAL_U64,
            self.HAL_PORT: hal_pb2.HAL_PORT,
        }
        return mapping.get(hal_type, hal_pb2.HAL_TYPE_UNSPECIFIED)

    def _map_pin_direction(self, direction: int) -> int:
        """Map HAL pin direction to proto PinDirection enum."""
        mapping = {
            self.HAL_IN: hal_pb2.HAL_IN,
            self.HAL_OUT: hal_pb2.HAL_OUT,
            self.HAL_IO: hal_pb2.HAL_IO,
        }
        return mapping.get(direction, hal_pb2.PIN_DIR_UNSPECIFIED)

    def _map_param_direction(self, direction: int) -> int:
        """Map HAL param direction to proto ParamDirection enum."""
        mapping = {
            self.HAL_RO: hal_pb2.HAL_RO,
            self.HAL_RW: hal_pb2.HAL_RW,
        }
        return mapping.get(direction, hal_pb2.PARAM_DIR_UNSPECIFIED)

    def get_type_for_name(self, name: str) -> int:
        """Look up the HAL type for a pin, signal, or param by name."""
        # Check pins
        for pin in self._pins:
            if self._get(pin, 'name') == name:
                return self._get(pin, 'type', self.HAL_FLOAT)

        # Check signals
        for sig in self._signals:
            if self._get(sig, 'name') == name:
                return self._get(sig, 'type', self.HAL_FLOAT)

        # Check params (no type field, infer from value)
        for param in self._params:
            if self._get(param, 'name') == name:
                value = self._get(param, 'value', 0)
                return self._infer_type_from_value(value)

        # Default to float
        return self.HAL_FLOAT
