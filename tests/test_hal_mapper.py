"""
Unit tests for HalMapper class.

HalMapper is pure Python and can be fully tested without LinuxCNC.
"""

import pytest

from linuxcnc_grpc_server.hal_mapper import HalMapper
from linuxcnc_grpc_server._generated import hal_pb2


class TestHalMapperInit:
    """Test HalMapper initialization."""

    def test_init_with_empty_data(self):
        """Mapper initializes with empty data."""
        mapper = HalMapper(pins=[], signals=[], params=[])
        assert mapper._pins == []
        assert mapper._signals == []
        assert mapper._params == []

    def test_init_with_sample_data(self, mock_hal_pins, mock_hal_signals, mock_hal_params):
        """Mapper initializes with sample data."""
        mapper = HalMapper(
            pins=mock_hal_pins,
            signals=mock_hal_signals,
            params=mock_hal_params
        )
        assert len(mapper._pins) == 3
        assert len(mapper._signals) == 2
        assert len(mapper._params) == 3


class TestHalMapperGetHelper:
    """Test the _get helper method for case-insensitive dict access."""

    def test_get_uppercase_key(self):
        """Get value with uppercase key."""
        result = HalMapper._get({"NAME": "test"}, "name")
        assert result == "test"

    def test_get_lowercase_key(self):
        """Get value with lowercase key."""
        result = HalMapper._get({"name": "test"}, "name")
        assert result == "test"

    def test_get_with_default(self):
        """Get returns default when key not found."""
        result = HalMapper._get({}, "missing", "default")
        assert result == "default"


class TestHalMapperValue:
    """Test HAL value mapping."""

    def test_map_bit_value_true(self):
        """Map bit value True."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(True, HalMapper.HAL_BIT)
        assert result.bit_value is True

    def test_map_bit_value_false(self):
        """Map bit value False."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(False, HalMapper.HAL_BIT)
        assert result.bit_value is False

    def test_map_float_value(self):
        """Map float value."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(123.456, HalMapper.HAL_FLOAT)
        assert abs(result.float_value - 123.456) < 0.0001

    def test_map_float_value_none(self):
        """Map None as float defaults to 0.0."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(None, HalMapper.HAL_FLOAT)
        assert result.float_value == 0.0

    def test_map_s32_value(self):
        """Map signed 32-bit integer."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(-12345, HalMapper.HAL_S32)
        assert result.s32_value == -12345

    def test_map_u32_value(self):
        """Map unsigned 32-bit integer."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(0xFFFFFFFF, HalMapper.HAL_U32)
        assert result.u32_value == 0xFFFFFFFF

    def test_map_s64_value(self):
        """Map signed 64-bit integer."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(-9999999999, HalMapper.HAL_S64)
        assert result.s64_value == -9999999999

    def test_map_u64_value(self):
        """Map unsigned 64-bit integer."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(0xFFFFFFFFFFFFFFFF, HalMapper.HAL_U64)
        assert result.u64_value == 0xFFFFFFFFFFFFFFFF


class TestHalMapperTypeInference:
    """Test type inference from values."""

    def test_infer_bit_from_bool(self):
        """Infer HAL_BIT from bool value."""
        mapper = HalMapper([], [], [])
        assert mapper._infer_type_from_value(True) == HalMapper.HAL_BIT
        assert mapper._infer_type_from_value(False) == HalMapper.HAL_BIT

    def test_infer_float_from_float(self):
        """Infer HAL_FLOAT from float value."""
        mapper = HalMapper([], [], [])
        assert mapper._infer_type_from_value(3.14) == HalMapper.HAL_FLOAT

    def test_infer_s32_from_negative_int(self):
        """Infer HAL_S32 from negative integer."""
        mapper = HalMapper([], [], [])
        assert mapper._infer_type_from_value(-42) == HalMapper.HAL_S32

    def test_infer_s32_from_positive_int(self):
        """Infer HAL_S32 from positive integer within u32 range."""
        mapper = HalMapper([], [], [])
        assert mapper._infer_type_from_value(42) == HalMapper.HAL_S32

    def test_infer_u64_from_large_int(self):
        """Infer HAL_U64 from integer larger than u32."""
        mapper = HalMapper([], [], [])
        large_value = 0xFFFFFFFF + 1
        assert mapper._infer_type_from_value(large_value) == HalMapper.HAL_U64


class TestHalMapperPin:
    """Test HAL pin mapping."""

    def test_map_pin_basic(self, mock_hal_pins):
        """Map a basic pin."""
        mapper = HalMapper(mock_hal_pins, [], [])
        result = mapper.map_pin(mock_hal_pins[0])

        assert result.name == "axis.x.pos-cmd"
        assert result.short_name == "pos-cmd"
        assert result.component == "axis.x"
        assert result.type == hal_pb2.HAL_FLOAT
        assert result.direction == hal_pb2.HAL_OUT

    def test_map_pin_bit_type(self, mock_hal_pins):
        """Map a bit-type pin."""
        mapper = HalMapper(mock_hal_pins, [], [])
        result = mapper.map_pin(mock_hal_pins[2])

        assert result.name == "iocontrol.0.emc-enable-in"
        assert result.type == hal_pb2.HAL_BIT
        assert result.direction == hal_pb2.HAL_IN


class TestHalMapperSignal:
    """Test HAL signal mapping."""

    def test_map_signal(self, mock_hal_signals):
        """Map a signal."""
        mapper = HalMapper([], mock_hal_signals, [])
        result = mapper.map_signal(mock_hal_signals[0])

        assert result.name == "x-pos-cmd"
        assert result.type == hal_pb2.HAL_FLOAT
        assert result.driver == "axis.x.pos-cmd"


class TestHalMapperParam:
    """Test HAL parameter mapping."""

    def test_map_param_rw(self, mock_hal_params):
        """Map a read-write parameter."""
        mapper = HalMapper([], [], mock_hal_params)
        result = mapper.map_param(mock_hal_params[0])

        assert result.name == "pid.x.Pgain"
        assert result.short_name == "Pgain"
        assert result.component == "pid.x"
        assert result.direction == hal_pb2.HAL_RW

    def test_map_param_ro(self, mock_hal_params):
        """Map a read-only parameter."""
        mapper = HalMapper([], [], mock_hal_params)
        result = mapper.map_param(mock_hal_params[2])

        assert result.name == "motion.servo.overruns"
        assert result.direction == hal_pb2.HAL_RO


class TestHalMapperComponents:
    """Test component derivation."""

    def test_derive_components_from_pins(self, mock_hal_pins):
        """Derive components from pin names."""
        mapper = HalMapper(mock_hal_pins, [], [])

        # Components should be derived from pin name prefixes
        assert "axis.x" in mapper._components
        assert "spindle.0" in mapper._components
        assert "iocontrol.0" in mapper._components

    def test_component_has_pins(self, mock_hal_pins):
        """Components track their pins."""
        mapper = HalMapper(mock_hal_pins, [], [])

        axis_comp = mapper._components.get("axis.x")
        assert axis_comp is not None
        assert "axis.x.pos-cmd" in axis_comp["pins"]

    def test_component_includes_params(self, mock_hal_pins, mock_hal_params):
        """Components include their parameters."""
        # Create a param that matches an existing component
        params = [{"NAME": "axis.x.gain", "DIRECTION": 192, "VALUE": 1.0}]
        mapper = HalMapper(mock_hal_pins, [], params)

        axis_comp = mapper._components.get("axis.x")
        assert axis_comp is not None
        assert "axis.x.gain" in axis_comp["params"]


class TestHalMapperTypeMapping:
    """Test HAL type enum mapping."""

    def test_map_hal_type_bit(self):
        """Map HAL_BIT type."""
        mapper = HalMapper([], [], [])
        assert mapper._map_hal_type(HalMapper.HAL_BIT) == hal_pb2.HAL_BIT

    def test_map_hal_type_float(self):
        """Map HAL_FLOAT type."""
        mapper = HalMapper([], [], [])
        assert mapper._map_hal_type(HalMapper.HAL_FLOAT) == hal_pb2.HAL_FLOAT

    def test_map_hal_type_unknown(self):
        """Unknown type maps to UNSPECIFIED."""
        mapper = HalMapper([], [], [])
        assert mapper._map_hal_type(999) == hal_pb2.HAL_TYPE_UNSPECIFIED


class TestHalMapperDirectionMapping:
    """Test direction enum mapping."""

    def test_map_pin_direction_in(self):
        """Map HAL_IN direction."""
        mapper = HalMapper([], [], [])
        assert mapper._map_pin_direction(HalMapper.HAL_IN) == hal_pb2.HAL_IN

    def test_map_pin_direction_out(self):
        """Map HAL_OUT direction."""
        mapper = HalMapper([], [], [])
        assert mapper._map_pin_direction(HalMapper.HAL_OUT) == hal_pb2.HAL_OUT

    def test_map_pin_direction_io(self):
        """Map HAL_IO direction."""
        mapper = HalMapper([], [], [])
        assert mapper._map_pin_direction(HalMapper.HAL_IO) == hal_pb2.HAL_IO

    def test_map_param_direction_ro(self):
        """Map HAL_RO direction."""
        mapper = HalMapper([], [], [])
        assert mapper._map_param_direction(HalMapper.HAL_RO) == hal_pb2.HAL_RO

    def test_map_param_direction_rw(self):
        """Map HAL_RW direction."""
        mapper = HalMapper([], [], [])
        assert mapper._map_param_direction(HalMapper.HAL_RW) == hal_pb2.HAL_RW


class TestHalMapperFullStatus:
    """Test full status mapping."""

    def test_map_to_proto(self, mock_hal_pins, mock_hal_signals, mock_hal_params):
        """Map full HAL status to protobuf."""
        mapper = HalMapper(mock_hal_pins, mock_hal_signals, mock_hal_params)
        status = mapper.map_to_proto()

        assert len(status.pins) == 3
        assert len(status.signals) == 2
        assert len(status.params) == 3
        assert len(status.components) > 0
        assert status.timestamp > 0

    def test_map_to_proto_empty(self):
        """Map empty HAL status."""
        mapper = HalMapper([], [], [])
        status = mapper.map_to_proto()

        assert len(status.pins) == 0
        assert len(status.signals) == 0
        assert len(status.params) == 0


class TestHalMapperGetTypeForName:
    """Test type lookup by name."""

    def test_get_type_for_pin_name(self, mock_hal_pins):
        """Get type for a pin by name."""
        mapper = HalMapper(mock_hal_pins, [], [])
        hal_type = mapper.get_type_for_name("axis.x.pos-cmd")
        assert hal_type == HalMapper.HAL_FLOAT

    def test_get_type_for_signal_name(self, mock_hal_signals):
        """Get type for a signal by name."""
        mapper = HalMapper([], mock_hal_signals, [])
        hal_type = mapper.get_type_for_name("x-pos-cmd")
        assert hal_type == HalMapper.HAL_FLOAT

    def test_get_type_for_unknown_name(self):
        """Get type for unknown name defaults to FLOAT."""
        mapper = HalMapper([], [], [])
        hal_type = mapper.get_type_for_name("nonexistent")
        assert hal_type == HalMapper.HAL_FLOAT
