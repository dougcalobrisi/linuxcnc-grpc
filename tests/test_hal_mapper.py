"""
Unit tests for HalMapper class.

HalMapper is pure Python and can be fully tested without LinuxCNC.
"""

import math
from types import SimpleNamespace

import pytest

from linuxcnc_grpc.hal_mapper import (
    HalMapper,
    HalSystemInfo,
    read_hal_message_level,
    read_hal_system_info,
    read_hal_type_constants,
    values_differ,
)
from linuxcnc_pb import hal_pb2


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

    def test_map_float_value_none_has_no_value(self):
        """A float that could not be read is not reported as 0.0."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(None, HalMapper.HAL_FLOAT)
        assert result.WhichOneof("value") is None

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

    def test_signed_value_too_wide_for_s32_is_reported_as_s64(self):
        """Where HAL integers are 64 bits wide the signed type keeps its number."""
        mapper = HalMapper([], [], [])
        proto_type, result = mapper.describe_value(-9999999999, HalMapper.HAL_S32)
        assert proto_type == hal_pb2.HAL_S64
        assert result.s64_value == -9999999999

    def test_unsigned_value_too_wide_for_u32_is_reported_as_u64(self):
        """Where HAL integers are 64 bits wide the unsigned type keeps its number."""
        mapper = HalMapper([], [], [])
        proto_type, result = mapper.describe_value(0xFFFFFFFFFFFFFFFF, HalMapper.HAL_U32)
        assert proto_type == hal_pb2.HAL_U64
        assert result.u64_value == 0xFFFFFFFFFFFFFFFF

    @pytest.mark.parametrize("hal_type", [HalMapper.HAL_S32, HalMapper.HAL_U32, None])
    @pytest.mark.parametrize("value", [
        -(2**63), -(2**31) - 1, -(2**31), -1, 0, 1, 2**31 - 1, 2**31,
        2**32 - 1, 2**32, 2**63 - 1, 2**63, 2**64 - 1,
    ])
    def test_no_integer_hal_can_hold_raises(self, hal_type, value):
        """Every 64-bit integer maps to a field that holds exactly that value."""
        mapper = HalMapper([], [], [])
        result = mapper.map_value(value, hal_type)
        if hal_type == HalMapper.HAL_U32 and -(2**31) <= value < 0:
            # A u32 that arrived through a 32-bit C long; see the next test.
            value += 2**32
        assert getattr(result, result.WhichOneof("value")) == value

    def test_u32_that_arrives_negative_is_read_as_unsigned(self):
        """Where C long is 32 bits wide, a u32 of 2^31 or more arrives negative."""
        mapper = HalMapper([], [], [])
        proto_type, result = mapper.describe_value(-1, HalMapper.HAL_U32)
        assert proto_type == hal_pb2.HAL_U32
        assert result.u32_value == 0xFFFFFFFF

    def test_integer_beyond_64_bits_has_no_value(self):
        """An impossible integer is reported without a value, not an exception."""
        mapper = HalMapper([], [], [])
        proto_type, result = mapper.describe_value(2**64, HalMapper.HAL_U32)
        assert proto_type == hal_pb2.HAL_TYPE_UNSPECIFIED
        assert result.WhichOneof("value") is None

    def test_port_has_type_but_no_value(self):
        """A port is labelled as a port and gets no made-up value."""
        mapper = HalMapper([], [], [])
        proto_type, result = mapper.describe_value(1234, HalMapper.HAL_PORT)
        assert proto_type == hal_pb2.HAL_PORT
        assert result.WhichOneof("value") is None

    def test_port_number_is_the_one_from_hal_h(self):
        """LinuxCNC 2.9 defines HAL_PORT = 5; 5 must not read as a 64-bit integer."""
        mapper = HalMapper([], [], [])
        assert HalMapper.HAL_PORT == 5
        assert mapper._map_hal_type(5) == hal_pb2.HAL_PORT

    def test_unknown_type_and_unreadable_value_has_no_value(self):
        """What 2.9 returns for a port (TYPE None, VALUE None) is not a float 0.0."""
        mapper = HalMapper([], [], [])
        proto_type, result = mapper.describe_value(None, None)
        assert proto_type == hal_pb2.HAL_TYPE_UNSPECIFIED
        assert result.WhichOneof("value") is None


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

    def test_infer_unsigned_from_int_above_s32(self):
        """An integer above 2147483647 can only be unsigned."""
        mapper = HalMapper([], [], [])
        assert mapper._infer_type_from_value(2**31 - 1) == HalMapper.HAL_S32
        assert mapper._infer_type_from_value(2**31) == HalMapper.HAL_U32
        assert mapper._infer_type_from_value(0xFFFFFFFF) == HalMapper.HAL_U32
        assert mapper._infer_type_from_value(0xFFFFFFFF + 1) == HalMapper.HAL_U32

    def test_infer_nothing_from_none(self):
        """No value, no type."""
        mapper = HalMapper([], [], [])
        assert mapper._infer_type_from_value(None) is None

    def test_values_of_one_item_share_a_type(self):
        """A u32 that crosses 2147483647 is u32 on both sides of the change."""
        mapper = HalMapper([], [], [])
        assert mapper.infer_type_from_values(5, 2**31) == HalMapper.HAL_U32
        assert mapper.infer_type_from_values(2**31, 5) == HalMapper.HAL_U32
        assert mapper.infer_type_from_values(5, 6) == HalMapper.HAL_S32
        assert mapper.infer_type_from_values(1.0, 2.0) == HalMapper.HAL_FLOAT


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


    def test_port_pin_as_listed_by_2_9_has_no_type_or_value(self):
        """2.9 lists a port pin with TYPE None and VALUE None."""
        port_pin = {"NAME": "streamer.0.port", "VALUE": None, "DIRECTION": 32, "TYPE": None}
        mapper = HalMapper([port_pin], [], [])
        result = mapper.map_pin(port_pin)

        assert result.type == hal_pb2.HAL_TYPE_UNSPECIFIED
        assert result.value.WhichOneof("value") is None
        assert result.direction == hal_pb2.HAL_OUT

    def test_port_pin_with_type_number_is_a_port(self):
        """A build that lists TYPE 5 for a port gets HAL_PORT, not HAL_S64."""
        port_pin = {"NAME": "streamer.0.port", "VALUE": 77, "DIRECTION": 32, "TYPE": 5}
        mapper = HalMapper([port_pin], [], [])
        result = mapper.map_pin(port_pin)

        assert result.type == hal_pb2.HAL_PORT
        assert result.value.WhichOneof("value") is None

    def test_has_writer_uses_the_hal_answer(self, mock_hal_pins):
        """has_writer is what hal.pin_has_writer said, not the pin direction."""
        pin_writers = {
            "axis.x.pos-cmd": False,  # an output pin that is not linked
            "iocontrol.0.emc-enable-in": True,  # an input on a driven signal
        }
        mapper = HalMapper(mock_hal_pins, [], [], pin_writers=pin_writers)

        assert mapper.map_pin(mock_hal_pins[0]).has_writer is False
        assert mapper.map_pin(mock_hal_pins[2]).has_writer is True

    def test_has_writer_without_hal_answer_comes_from_signal_drivers(
        self, mock_hal_pins, mock_hal_signals
    ):
        """Without an answer from HAL only a signal's driver is known to have a writer."""
        unlinked_output_pin = {"NAME": "comp.0.out", "TYPE": 2, "DIRECTION": 32, "VALUE": 0.0}
        mapper = HalMapper(mock_hal_pins + [unlinked_output_pin], mock_hal_signals, [])

        assert mapper.map_pin(mock_hal_pins[0]).has_writer is True
        assert mapper.map_pin(unlinked_output_pin).has_writer is False

    def test_pin_without_direction_is_unspecified(self):
        """A missing direction is not reported as an input."""
        pin = {"NAME": "comp.0.pin", "TYPE": 1, "VALUE": False}
        mapper = HalMapper([pin], [], [])
        assert mapper.map_pin(pin).direction == hal_pb2.PIN_DIR_UNSPECIFIED


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


    @pytest.mark.parametrize("value", [2**31, 3000000000, 2**32 - 1])
    def test_u32_param_above_s32_range(self, value):
        """A u32 parameter in the upper half of its range maps as u32."""
        param = {"NAME": "hm2.0.watchdog.timeout_ns", "DIRECTION": 192, "VALUE": value}
        mapper = HalMapper([], [], [param])
        result = mapper.map_param(param)

        assert result.type == hal_pb2.HAL_U32
        assert result.value.u32_value == value

    def test_param_type_is_used_when_the_hal_module_gives_one(self):
        """A small u32 parameter is u32 when the listing has a TYPE."""
        param = {"NAME": "comp.0.count", "DIRECTION": 64, "VALUE": 7, "TYPE": 4}
        mapper = HalMapper([], [], [param])
        result = mapper.map_param(param)

        assert result.type == hal_pb2.HAL_U32
        assert result.value.u32_value == 7

    def test_small_integer_param_without_type_is_called_s32(self):
        """Without a TYPE, 0..2147483647 fits both and is reported as s32."""
        param = {"NAME": "comp.0.count", "DIRECTION": 64, "VALUE": 7}
        mapper = HalMapper([], [], [param])
        result = mapper.map_param(param)

        assert result.type == hal_pb2.HAL_S32
        assert result.value.s32_value == 7

    def test_unreadable_param_as_listed_by_2_9(self):
        """2.9 lists a param it cannot read with only NAME and VALUE None."""
        param = {"NAME": "comp.0.port-param", "VALUE": None}
        mapper = HalMapper([], [], [param])
        result = mapper.map_param(param)

        assert result.type == hal_pb2.HAL_TYPE_UNSPECIFIED
        assert result.value.WhichOneof("value") is None
        assert result.direction == hal_pb2.PARAM_DIR_UNSPECIFIED

    def test_full_status_with_upper_range_u32_param(self, mock_hal_pins, mock_hal_signals):
        """One large u32 parameter does not break the whole status."""
        params = [{"NAME": "comp.0.big", "DIRECTION": 192, "VALUE": 4000000000}]
        status = HalMapper(mock_hal_pins, mock_hal_signals, params).map_to_proto()

        assert status.params[0].value.u32_value == 4000000000
        assert len(status.pins) == 3


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


    def test_unknown_component_fields_stay_at_defaults(self, mock_hal_pins):
        """Nothing is known about readiness, kind or process of an inferred group."""
        mapper = HalMapper(mock_hal_pins, [], [])
        component = mapper.map_component(mapper._components["axis.x"])

        assert component.name == "axis.x"
        assert list(component.pins) == ["axis.x.pos-cmd"]
        assert component.ready is False
        assert component.type == 0
        assert component.pid == 0


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

    def test_system_fields_default_to_unknown(self):
        """Without facts from the hal module no system flag is claimed."""
        status = HalMapper([], [], []).map_to_proto()

        assert status.message_level == hal_pb2.MSG_LEVEL_UNSPECIFIED
        assert status.is_sim is False
        assert status.is_rt is False
        assert status.is_userspace is False
        assert status.kernel_version == ""

    def test_system_fields_come_from_system_info(self):
        """The status carries what was read from the hal module."""
        system_info = HalSystemInfo(
            message_level=hal_pb2.MSG_DBG, is_sim=True, is_rt=False,
            is_userspace=True, kernel_version="6.1.0-rt",
        )
        status = HalMapper([], [], [], system_info=system_info).map_to_proto()

        assert status.message_level == hal_pb2.MSG_DBG
        assert status.is_sim is True
        assert status.is_rt is False
        assert status.is_userspace is True
        assert status.kernel_version == "6.1.0-rt"

    def test_item_that_cannot_be_mapped_is_left_out(self, mock_hal_pins):
        """One broken entry does not cost the rest of the listing."""
        pins = [{"NAME": 12345, "TYPE": 2, "DIRECTION": 32, "VALUE": 1.0}] + mock_hal_pins
        status = HalMapper(mock_hal_pins, [], []).map_to_proto()
        assert len(status.pins) == 3

        mapper = HalMapper(mock_hal_pins, [], [])
        mapped_pins = mapper.map_each(mapper.map_pin, pins)
        assert [pin.name for pin in mapped_pins] == [pin["NAME"] for pin in mock_hal_pins]

    @pytest.mark.parametrize("unusable_name", [None, 12345, ["a", "list"]])
    def test_malformed_pin_does_not_abort_the_full_status(
        self, mock_hal_pins, mock_hal_signals, mock_hal_params, caplog, unusable_name
    ):
        """A pin whose name is not text is left out; everything else is reported."""
        malformed_pin = {"NAME": unusable_name, "TYPE": 2, "DIRECTION": 32, "VALUE": 1.0}
        pins = [mock_hal_pins[0], malformed_pin] + mock_hal_pins[1:]

        with caplog.at_level("WARNING", logger="linuxcnc_grpc.hal_mapper"):
            status = HalMapper(pins, mock_hal_signals, mock_hal_params).map_to_proto()

        expected_status = HalMapper(
            mock_hal_pins, mock_hal_signals, mock_hal_params
        ).map_to_proto()
        assert [pin.name for pin in status.pins] == [pin["NAME"] for pin in mock_hal_pins]
        assert list(status.components) == list(expected_status.components)
        assert len(status.signals) == len(mock_hal_signals)
        assert len(status.params) == len(mock_hal_params)
        assert "could not be grouped into a component" in caplog.text

    def test_malformed_param_does_not_abort_the_full_status(
        self, mock_hal_pins, mock_hal_params, caplog
    ):
        """The same for a parameter whose name is missing."""
        malformed_param = {"NAME": None, "DIRECTION": 192, "VALUE": 1.0}
        params = [malformed_param] + mock_hal_params

        with caplog.at_level("WARNING", logger="linuxcnc_grpc.hal_mapper"):
            status = HalMapper(mock_hal_pins, [], params).map_to_proto()

        expected_status = HalMapper(mock_hal_pins, [], mock_hal_params).map_to_proto()
        assert [param.name for param in status.params] == [
            param["NAME"] for param in mock_hal_params
        ]
        assert list(status.components) == list(expected_status.components)
        assert "could not be grouped into a component" in caplog.text

    def test_malformed_signal_does_not_abort_the_full_status(
        self, mock_hal_pins, mock_hal_signals, caplog
    ):
        """A signal whose name or driver cannot be indexed is left out of the listing."""
        malformed_signals = [
            {"NAME": ["a", "list"], "TYPE": 2, "VALUE": 1.0, "DRIVER": "axis.x.pos-cmd"},
            {"NAME": "odd-driver", "TYPE": 2, "VALUE": 1.0, "DRIVER": ["a", "list"]},
        ]

        with caplog.at_level("WARNING", logger="linuxcnc_grpc.hal_mapper"):
            mapper = HalMapper(mock_hal_pins, malformed_signals + mock_hal_signals, [])
            status = mapper.map_to_proto()
            declared_types = mapper.declared_types()

        assert [signal.name for signal in status.signals] == [
            signal["NAME"] for signal in mock_hal_signals
        ]
        assert len(status.pins) == len(mock_hal_pins)
        assert "odd-driver" in declared_types
        assert "could not be indexed" in caplog.text

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
        """An unknown name has no type."""
        mapper = HalMapper([], [], [])
        hal_type = mapper.get_type_for_name("nonexistent")
        assert hal_type is None

    def test_get_type_for_upper_range_u32_param(self):
        """A param without TYPE and a value above s32 is u32."""
        params = [{"NAME": "comp.0.big", "DIRECTION": 192, "VALUE": 4000000000}]
        mapper = HalMapper([], [], params)
        assert mapper.get_type_for_name("comp.0.big") == HalMapper.HAL_U32

    def test_declared_types_lists_every_name(self, mock_hal_pins, mock_hal_signals, mock_hal_params):
        """Pins and signals carry their TYPE; a param without one is None."""
        mapper = HalMapper(mock_hal_pins, mock_hal_signals, mock_hal_params)
        declared_types = mapper.declared_types()

        assert declared_types["axis.x.pos-cmd"] == HalMapper.HAL_FLOAT
        assert declared_types["x-pos-cmd"] == HalMapper.HAL_FLOAT
        assert declared_types["pid.x.Pgain"] is None


class TestReadHalTypeConstants:
    """Type numbers are read from the hal module, with built-in fallbacks."""

    def test_reads_2_9_names(self):
        """LinuxCNC 2.9 has HAL_BIT/FLOAT/S32/U32 and no HAL_PORT."""
        hal_module = SimpleNamespace(HAL_BIT=1, HAL_FLOAT=2, HAL_S32=3, HAL_U32=4)
        assert read_hal_type_constants(hal_module) == {
            "HAL_BIT": 1, "HAL_FLOAT": 2, "HAL_S32": 3, "HAL_U32": 4,
        }
        assert HalMapper([], [], [], type_constants={}).HAL_PORT == 5

    def test_prefers_newer_names(self):
        """Newer builds name the types HAL_BOOL/REAL/SINT/UINT and export HAL_PORT."""
        hal_module = SimpleNamespace(
            HAL_BOOL=11, HAL_REAL=12, HAL_SINT=13, HAL_UINT=14, HAL_PORT=15,
            HAL_BIT=1, HAL_FLOAT=2, HAL_S32=3, HAL_U32=4,
        )
        assert read_hal_type_constants(hal_module) == {
            "HAL_BIT": 11, "HAL_FLOAT": 12, "HAL_S32": 13, "HAL_U32": 14, "HAL_PORT": 15,
        }

    def test_ignores_values_that_are_not_integers(self):
        """Only a real integer is taken as a type number."""
        hal_module = SimpleNamespace(HAL_BIT="1", HAL_FLOAT=None, HAL_S32=True, HAL_U32=4)
        assert read_hal_type_constants(hal_module) == {"HAL_U32": 4}

    def test_mapper_uses_the_given_numbers(self):
        """A pin is typed by the numbers of the hal module in use."""
        pin = {"NAME": "comp.0.flag", "TYPE": 11, "DIRECTION": 16, "VALUE": True}
        mapper = HalMapper([pin], [], [], type_constants={"HAL_BIT": 11})
        result = mapper.map_pin(pin)

        assert result.type == hal_pb2.HAL_BIT
        assert result.value.bit_value is True
        assert HalMapper.HAL_BIT == 1


class TestReadHalSystemInfo:
    """System flags are read from the hal module, with fallbacks."""

    @staticmethod
    def _hal_module(**attributes):
        hal_module = SimpleNamespace(
            MSG_NONE=0, MSG_ERR=1, MSG_WARN=2, MSG_INFO=3, MSG_DBG=4, MSG_ALL=5,
        )
        for attribute_name, attribute_value in attributes.items():
            setattr(hal_module, attribute_name, attribute_value)
        return hal_module

    def test_reads_sim_build_flags(self):
        """A simulator build: is_sim 1, is_rt 0, is_userspace 1."""
        hal_module = self._hal_module(
            is_sim=1, is_rt=0, is_userspace=1, kernel_version="Not Available",
            get_msg_level=lambda: 1,
        )
        system_info = read_hal_system_info(hal_module)

        assert system_info.is_sim is True
        assert system_info.is_rt is False
        assert system_info.is_userspace is True
        assert system_info.kernel_version == "Not Available"
        assert system_info.message_level == hal_pb2.MSG_ERR

    def test_reads_realtime_build_flags(self):
        """A realtime build: is_sim 0, is_rt 1."""
        hal_module = self._hal_module(
            is_sim=0, is_rt=1, is_userspace=0, kernel_version="4.19.0-rtai",
            get_msg_level=lambda: 3,
        )
        system_info = read_hal_system_info(hal_module)

        assert system_info.is_sim is False
        assert system_info.is_rt is True
        assert system_info.is_userspace is False
        assert system_info.kernel_version == "4.19.0-rtai"
        assert system_info.message_level == hal_pb2.MSG_INFO

    def test_missing_attributes_fall_back_to_defaults(self):
        """A module without these attributes gives the "not known" defaults."""
        assert read_hal_system_info(SimpleNamespace()) == HalSystemInfo()

    @pytest.mark.parametrize("rtapi_level,proto_level", [
        (0, hal_pb2.MSG_NONE), (1, hal_pb2.MSG_ERR), (2, hal_pb2.MSG_WARN),
        (3, hal_pb2.MSG_INFO), (4, hal_pb2.MSG_DBG), (5, hal_pb2.MSG_ALL),
        (17, hal_pb2.MSG_LEVEL_UNSPECIFIED),
    ])
    def test_message_level_numbers(self, rtapi_level, proto_level):
        """RTAPI numbers its levels from 0; the proto enum starts at 1."""
        with_module_names = self._hal_module(get_msg_level=lambda: rtapi_level)
        without_module_names = SimpleNamespace(get_msg_level=lambda: rtapi_level)

        assert read_hal_message_level(with_module_names) == proto_level
        assert read_hal_message_level(without_module_names) == proto_level

    def test_message_level_that_cannot_be_read(self):
        """A failing get_msg_level is "not known", not an error."""
        def failing_get_msg_level():
            raise RuntimeError("no HAL")

        hal_module = self._hal_module(get_msg_level=failing_get_msg_level)
        assert read_hal_message_level(hal_module) == hal_pb2.MSG_LEVEL_UNSPECIFIED


class TestValuesDiffer:
    """Change detection for watched values."""

    def test_nan_equals_nan(self):
        """A value that stays NaN has not changed."""
        assert values_differ(math.nan, float("nan")) is False

    def test_nan_differs_from_a_number(self):
        """Becoming NaN, or leaving it, is a change."""
        assert values_differ(1.0, math.nan) is True
        assert values_differ(math.nan, 1.0) is True

    def test_plain_values(self):
        """Other values compare as usual."""
        assert values_differ(1.0, 1.0) is False
        assert values_differ(1.0, 2.0) is True
        assert values_differ(False, True) is True
