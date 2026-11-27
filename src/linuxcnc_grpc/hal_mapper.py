"""
HAL Python API data to protobuf mapper.

Maps hal.get_info_*() dictionaries to HalSystemStatus protobuf messages.

Note: The HAL Python API returns dicts with UPPERCASE keys:
- Pins: NAME, VALUE, DIRECTION, TYPE
- Signals: NAME, VALUE, DRIVER, TYPE
- Params: NAME, DIRECTION, VALUE (LinuxCNC 2.9 gives no TYPE; newer builds do)

An item of a type the hal module cannot read (a port in 2.9) comes back with
TYPE and VALUE set to None. Such items are reported without a value.

This module does not import hal, so it can be used and tested without
LinuxCNC. The service passes in what it read from the hal module.
"""

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from linuxcnc_pb import hal_pb2

logger = logging.getLogger(__name__)

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
UINT32_MAX = 2**32 - 1
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
UINT64_MAX = 2**64 - 1

# For each HalMapper type attribute, the hal module names that carry its
# number. The newer name is tried first: newer builds warn on the old one.
HAL_TYPE_CONSTANT_NAMES = {
    'HAL_BIT': ('HAL_BOOL', 'HAL_BIT'),
    'HAL_FLOAT': ('HAL_REAL', 'HAL_FLOAT'),
    'HAL_S32': ('HAL_SINT', 'HAL_S32'),
    'HAL_U32': ('HAL_UINT', 'HAL_U32'),
    'HAL_PORT': ('HAL_PORT',),
}

# RTAPI message level names in the hal module, their numbers in rtapi.h
# (used when the module lacks the name) and the proto value for each.
MESSAGE_LEVELS = (
    ('MSG_NONE', 0, hal_pb2.MSG_NONE),
    ('MSG_ERR', 1, hal_pb2.MSG_ERR),
    ('MSG_WARN', 2, hal_pb2.MSG_WARN),
    ('MSG_INFO', 3, hal_pb2.MSG_INFO),
    ('MSG_DBG', 4, hal_pb2.MSG_DBG),
    ('MSG_ALL', 5, hal_pb2.MSG_ALL),
)


def _is_plain_integer(value: Any) -> bool:
    """True for an int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _read_flag(hal_module: Any, attribute_name: str) -> bool:
    """Read a 0/1 module constant; False when it is missing or not a number."""
    try:
        flag = getattr(hal_module, attribute_name, None)
    except Exception:
        return False
    return bool(flag) if isinstance(flag, int) else False


def read_hal_type_constants(hal_module: Any) -> Dict[str, int]:
    """
    Read the HAL type numbers from the hal module.

    Returns only the ones the module has as real integers; HalMapper keeps
    its built-in number for the rest. LinuxCNC 2.9 has no HAL_PORT constant
    in the module, for example.
    """
    type_constants: Dict[str, int] = {}
    for mapper_attribute, module_names in HAL_TYPE_CONSTANT_NAMES.items():
        for module_name in module_names:
            try:
                number = getattr(hal_module, module_name, None)
            except Exception:
                number = None
            if _is_plain_integer(number):
                type_constants[mapper_attribute] = int(number)
                break
    return type_constants


def read_hal_message_level(hal_module: Any) -> int:
    """
    Read the RTAPI message level as a proto MessageLevel.

    Returns MSG_LEVEL_UNSPECIFIED when the module cannot say.
    """
    try:
        level = hal_module.get_msg_level()
    except Exception:
        return hal_pb2.MSG_LEVEL_UNSPECIFIED
    if not _is_plain_integer(level):
        return hal_pb2.MSG_LEVEL_UNSPECIFIED
    for module_name, rtapi_number, proto_level in MESSAGE_LEVELS:
        module_number = getattr(hal_module, module_name, None)
        if not _is_plain_integer(module_number):
            module_number = rtapi_number
        if level == module_number:
            return proto_level
    return hal_pb2.MSG_LEVEL_UNSPECIFIED


@dataclass(frozen=True)
class HalSystemInfo:
    """System-wide HAL facts. The defaults mean "not known"."""

    message_level: int = hal_pb2.MSG_LEVEL_UNSPECIFIED
    is_sim: bool = False
    is_rt: bool = False
    is_userspace: bool = False
    kernel_version: str = ""


def read_hal_system_info(hal_module: Any) -> HalSystemInfo:
    """
    Read is_sim, is_rt, is_userspace, kernel_version and the message level.

    A missing attribute leaves its field at the proto default.
    """
    try:
        kernel_version = getattr(hal_module, 'kernel_version', "")
    except Exception:
        kernel_version = ""
    return HalSystemInfo(
        message_level=read_hal_message_level(hal_module),
        is_sim=_read_flag(hal_module, 'is_sim'),
        is_rt=_read_flag(hal_module, 'is_rt'),
        is_userspace=_read_flag(hal_module, 'is_userspace'),
        kernel_version=kernel_version if isinstance(kernel_version, str) else "",
    )


def values_differ(previous_value: Any, current_value: Any) -> bool:
    """Compare two HAL values; two NaNs count as the same value."""
    if (
        isinstance(previous_value, float) and isinstance(current_value, float)
        and math.isnan(previous_value) and math.isnan(current_value)
    ):
        return False
    return previous_value != current_value


class HalMapper:
    """Maps HAL introspection data to protobuf messages."""

    # HAL type numbers from hal.h, used unless the hal module gives its own.
    # They are the same in LinuxCNC 2.8 and 2.9 (2.8 has no port type). HAL
    # has no separate 64-bit types: where integers are 64 bits wide they
    # still carry numbers 3 and 4, and wide values are reported as s64/u64.
    HAL_BIT = 1
    HAL_FLOAT = 2
    HAL_S32 = 3
    HAL_U32 = 4
    HAL_PORT = 5

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
        params: List[Dict[str, Any]],
        type_constants: Optional[Dict[str, int]] = None,
        pin_writers: Optional[Dict[str, bool]] = None,
        system_info: Optional[HalSystemInfo] = None,
    ):
        """
        Initialize mapper with HAL introspection data.

        Args:
            pins: Result from hal.get_info_pins()
            signals: Result from hal.get_info_signals()
            params: Result from hal.get_info_params()
            type_constants: Result from read_hal_type_constants(), if any
            pin_writers: hal.pin_has_writer() answers by pin name, if read
            system_info: Result from read_hal_system_info(), if any
        """
        self._pins = pins
        self._signals = signals
        self._params = params
        for mapper_attribute, number in (type_constants or {}).items():
            if mapper_attribute in HAL_TYPE_CONSTANT_NAMES:
                setattr(self, mapper_attribute, number)
        self._pin_writers = pin_writers
        self._system_info = system_info or HalSystemInfo()
        self._signal_pins = self._build_signal_pin_map()
        self._driver_pin_names = {
            connection['driver'] for connection in self._signal_pins.values()
            if connection['driver'] and isinstance(connection['driver'], str)
        }
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
            try:
                sig_name = self._get(sig, 'name', '')
                driver = self._get(sig, 'driver', '')
                signal_map[sig_name] = {
                    'driver': driver,
                    'readers': []
                }
            except Exception as e:
                # A signal that cannot be indexed is skipped here, as
                # map_each skips one that cannot be mapped.
                logger.warning(f"Skipping HAL signal that could not be indexed ({sig!r}): {e}")

        # Note: HAL doesn't provide signal connection info in pin dicts,
        # so we can't determine readers from the pin data directly.
        # The driver comes from the signal info itself.

        return signal_map

    def _derive_components(self) -> Dict[str, Dict[str, Any]]:
        """
        Infer component groups from pin and param names.

        The hal module has no component list, so the name before the last
        dot stands in for the component. That is a naming group, not always
        a real HAL component, and components without pins or params are not
        seen. Nothing else about a component is known here.
        """
        components: Dict[str, Dict[str, Any]] = {}

        for item_kind, items in (('pins', self._pins), ('params', self._params)):
            for item in items:
                try:
                    name = self._get(item, 'name', '')
                    # Component name is everything before the last dot
                    comp_name = name.rsplit('.', 1)[0] if '.' in name else name
                    owner_id = self._get(item, 'owner', 0)
                    # A component first seen through a param has no pins.
                    component = components.setdefault(comp_name, {
                        'name': comp_name,
                        'id': owner_id,
                        'pins': [],
                        'params': [],
                    })
                    component[item_kind].append(name)
                except Exception as e:
                    # One odd item must not cost the client the whole
                    # listing; map_each leaves it out of its own list too.
                    logger.warning(
                        f"Skipping HAL item that could not be grouped into a component ({item!r}): {e}"
                    )

        return components

    @staticmethod
    def map_each(
        map_one: Callable[[Dict[str, Any]], Any],
        items: Iterable[Dict[str, Any]],
    ) -> List[Any]:
        """Map every item, leaving out (and logging) any that cannot be mapped."""
        mapped_items = []
        for item in items:
            try:
                mapped_items.append(map_one(item))
            except Exception as e:
                # One odd item must not cost the client the whole listing.
                logger.warning(f"Skipping HAL item that could not be mapped ({item!r}): {e}")
        return mapped_items

    def map_to_proto(self) -> hal_pb2.HalSystemStatus:
        """Map all HAL data to a complete HalSystemStatus message."""
        return hal_pb2.HalSystemStatus(
            timestamp=int(time.time() * 1e9),
            pins=self.map_each(self.map_pin, self._pins),
            signals=self.map_each(self.map_signal, self._signals),
            params=self.map_each(self.map_param, self._params),
            components=self.map_each(self.map_component, self._components.values()),
            message_level=self._system_info.message_level,
            is_sim=self._system_info.is_sim,
            is_rt=self._system_info.is_rt,
            is_userspace=self._system_info.is_userspace,
            kernel_version=self._system_info.kernel_version,
        )

    def _pin_has_writer(self, name: str) -> bool:
        """
        Whether the pin's signal is driven by an output pin.

        Uses the hal module's answer when the service read one. Without it,
        the only pins known to have a writer are the signal drivers.
        """
        if self._pin_writers is not None and name in self._pin_writers:
            return self._pin_writers[name]
        return name in self._driver_pin_names

    def map_pin(self, pin_info: Dict[str, Any]) -> hal_pb2.HalPinInfo:
        """Map a single pin info dict to HalPinInfo proto."""
        name = self._get(pin_info, 'name', '')
        short_name = name.rsplit('.', 1)[-1] if '.' in name else name
        component = name.rsplit('.', 1)[0] if '.' in name else ''
        direction = self._get(pin_info, 'direction')
        signal = self._get(pin_info, 'signal', '')
        proto_type, value = self.describe_value(
            self._get(pin_info, 'value'), self._get(pin_info, 'type')
        )

        return hal_pb2.HalPinInfo(
            name=name,
            short_name=short_name,
            component=component,
            type=proto_type,
            direction=self._map_pin_direction(direction),
            value=value,
            signal=signal if signal else '',
            has_writer=self._pin_has_writer(name)
        )

    def map_signal(self, signal_info: Dict[str, Any]) -> hal_pb2.HalSignalInfo:
        """Map a single signal info dict to HalSignalInfo proto."""
        name = self._get(signal_info, 'name', '')
        driver = self._get(signal_info, 'driver', '')
        proto_type, value = self.describe_value(
            self._get(signal_info, 'value'), self._get(signal_info, 'type')
        )

        # Get connected pins from our map
        # Note: readers list is always empty because the HAL API doesn't
        # expose signal-to-reader-pin connections. reader_count will be 0.
        conn = self._signal_pins.get(name, {'driver': driver, 'readers': []})

        return hal_pb2.HalSignalInfo(
            name=name,
            type=proto_type,
            value=value,
            driver=conn['driver'] or '',
            readers=conn['readers'],
            reader_count=len(conn['readers'])
        )

    def map_param(self, param_info: Dict[str, Any]) -> hal_pb2.HalParamInfo:
        """Map a single param info dict to HalParamInfo proto."""
        name = self._get(param_info, 'name', '')
        short_name = name.rsplit('.', 1)[-1] if '.' in name else name
        component = name.rsplit('.', 1)[0] if '.' in name else ''
        direction = self._get(param_info, 'direction')
        # The type is taken from TYPE when the hal module gives one and
        # otherwise worked out from the value.
        proto_type, value = self.describe_value(
            self._get(param_info, 'value'), self._get(param_info, 'type')
        )

        return hal_pb2.HalParamInfo(
            name=name,
            short_name=short_name,
            component=component,
            type=proto_type,
            direction=self._map_param_direction(direction),
            value=value
        )

    def _infer_type_from_value(self, value: Any) -> Optional[int]:
        """
        Work out a HAL type from a Python value; None when it cannot be told.

        An integer from 0 to 2147483647 fits both s32 and u32 and is called
        s32. Above that only u32 can hold it.
        """
        if isinstance(value, bool):
            return self.HAL_BIT
        if isinstance(value, float):
            return self.HAL_FLOAT
        if isinstance(value, int):
            return self.HAL_U32 if value > INT32_MAX else self.HAL_S32
        return None

    def infer_type_from_values(self, *values: Any) -> Optional[int]:
        """
        Work out one HAL type for several values of the same item.

        If any of them needs u32, all are u32, so the old and the new value
        of a change are reported in the same field.
        """
        inferred_types = [self._infer_type_from_value(value) for value in values]
        integer_types = (self.HAL_S32, self.HAL_U32)
        if self.HAL_U32 in inferred_types and all(
            inferred_type in integer_types for inferred_type in inferred_types
        ):
            return self.HAL_U32
        return inferred_types[-1] if inferred_types else None

    def map_component(self, comp_info: Dict[str, Any]) -> hal_pb2.HalComponentInfo:
        """
        Map an inferred component group to HalComponentInfo proto.

        Only the name and the pin and param lists are known. ready, type and
        pid stay at their proto defaults, which here mean "not known".
        """
        return hal_pb2.HalComponentInfo(
            name=comp_info.get('name', ''),
            id=comp_info.get('id', 0),
            ready=comp_info.get('ready', False),
            type=comp_info.get('type', 0),
            pid=comp_info.get('pid', 0),
            pins=comp_info.get('pins', []),
            params=comp_info.get('params', [])
        )

    def _known_types(self) -> Tuple[int, ...]:
        return (self.HAL_BIT, self.HAL_FLOAT, self.HAL_S32, self.HAL_U32, self.HAL_PORT)

    @staticmethod
    def _describe_integer(value: int, declared_unsigned: bool) -> Tuple[int, hal_pb2.HalValue]:
        """Put an integer in the narrowest field of its signedness that holds it."""
        unsigned_holders = (
            (0, UINT32_MAX, hal_pb2.HAL_U32, 'u32_value'),
            (0, UINT64_MAX, hal_pb2.HAL_U64, 'u64_value'),
        )
        signed_holders = (
            (INT32_MIN, INT32_MAX, hal_pb2.HAL_S32, 's32_value'),
            (INT64_MIN, INT64_MAX, hal_pb2.HAL_S64, 's64_value'),
        )
        if declared_unsigned:
            if INT32_MIN <= value < 0:
                # LinuxCNC 2.9 passes a u32 through a C long, which is 32
                # bits wide on some platforms and then reads as negative.
                value &= UINT32_MAX
            holders = unsigned_holders + signed_holders
        else:
            holders = signed_holders + unsigned_holders
        for lowest, highest, proto_type, field_name in holders:
            if lowest <= value <= highest:
                return proto_type, hal_pb2.HalValue(**{field_name: value})
        return hal_pb2.HAL_TYPE_UNSPECIFIED, hal_pb2.HalValue()

    def describe_value(self, value: Any, hal_type: Optional[int]) -> Tuple[int, hal_pb2.HalValue]:
        """
        Return the proto HalType and HalValue for a raw HAL value.

        hal_type is the HAL type number, or None (or an unknown number) when
        the hal module gave none; the type is then worked out from the value.
        Never raises for a value HAL can hold, and never makes a value up: a
        port, an unreadable value or an unknown type gets an empty HalValue.
        """
        if hal_type not in self._known_types():
            hal_type = self._infer_type_from_value(value)

        if hal_type == self.HAL_BIT:
            if value is None:
                return hal_pb2.HAL_BIT, hal_pb2.HalValue()
            return hal_pb2.HAL_BIT, hal_pb2.HalValue(bit_value=bool(value))
        if hal_type == self.HAL_FLOAT:
            if isinstance(value, (int, float)):
                return hal_pb2.HAL_FLOAT, hal_pb2.HalValue(float_value=float(value))
            return hal_pb2.HAL_FLOAT, hal_pb2.HalValue()
        if hal_type in (self.HAL_S32, self.HAL_U32):
            declared_unsigned = hal_type == self.HAL_U32
            if isinstance(value, int):
                return self._describe_integer(int(value), declared_unsigned)
            return self._map_hal_type(hal_type), hal_pb2.HalValue()
        if hal_type == self.HAL_PORT:
            # A port is a queue; the hal module has no value for it.
            return hal_pb2.HAL_PORT, hal_pb2.HalValue()
        return hal_pb2.HAL_TYPE_UNSPECIFIED, hal_pb2.HalValue()

    def map_value(self, value: Any, hal_type: Optional[int]) -> hal_pb2.HalValue:
        """Map a raw HAL value to HalValue proto; see describe_value."""
        return self.describe_value(value, hal_type)[1]

    def _map_hal_type(self, hal_type: Optional[int]) -> int:
        """Map HAL type constant to proto HalType enum."""
        mapping = {
            self.HAL_BIT: hal_pb2.HAL_BIT,
            self.HAL_FLOAT: hal_pb2.HAL_FLOAT,
            self.HAL_S32: hal_pb2.HAL_S32,
            self.HAL_U32: hal_pb2.HAL_U32,
            self.HAL_PORT: hal_pb2.HAL_PORT,
        }
        return mapping.get(hal_type, hal_pb2.HAL_TYPE_UNSPECIFIED)

    def _map_pin_direction(self, direction: Optional[int]) -> int:
        """Map HAL pin direction to proto PinDirection enum."""
        mapping = {
            self.HAL_IN: hal_pb2.HAL_IN,
            self.HAL_OUT: hal_pb2.HAL_OUT,
            self.HAL_IO: hal_pb2.HAL_IO,
        }
        return mapping.get(direction, hal_pb2.PIN_DIR_UNSPECIFIED)

    def _map_param_direction(self, direction: Optional[int]) -> int:
        """Map HAL param direction to proto ParamDirection enum."""
        mapping = {
            self.HAL_RO: hal_pb2.HAL_RO,
            self.HAL_RW: hal_pb2.HAL_RW,
        }
        return mapping.get(direction, hal_pb2.PARAM_DIR_UNSPECIFIED)

    def declared_types(self) -> Dict[str, Optional[int]]:
        """
        HAL type number by name for every pin, signal and param.

        The number is None where the hal module gave no type. When a name is
        used twice, the order follows hal.get_value(): param, pin, signal.
        """
        declared_types: Dict[str, Optional[int]] = {}
        for items in (self._signals, self._pins, self._params):
            for item in items:
                try:
                    declared_types[self._get(item, 'name')] = self._get(item, 'type')
                except Exception as e:
                    # An item whose name cannot be a key is no watch target.
                    logger.warning(f"Skipping HAL item with an unusable name ({item!r}): {e}")
        return declared_types

    def get_type_for_name(self, name: str) -> Optional[int]:
        """
        Look up the HAL type for a pin, signal, or param by name.

        Returns None for an unknown name or an item with no usable type. A
        param without a TYPE gets the type its current value suggests.
        """
        for items in (self._params, self._pins, self._signals):
            for item in items:
                if self._get(item, 'name') == name:
                    hal_type = self._get(item, 'type')
                    if hal_type in self._known_types():
                        return hal_type
                    return self._infer_type_from_value(self._get(item, 'value'))
        return None
