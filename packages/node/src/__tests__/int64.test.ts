/**
 * How 64-bit integers arrive in the generated TypeScript.
 *
 * Every int64 and uint64 field is a JavaScript number. Timestamps are sent in
 * nanoseconds, which do not fit a number exactly, and arrive as milliseconds.
 * Every other 64-bit field arrives unscaled, as the nearest number.
 *
 * The messages are built from decimal strings with the wire writer, so the
 * bytes hold the exact 64-bit value whatever a number could carry.
 */

import { BinaryReader, BinaryWriter } from '@bufbuild/protobuf/wire';
import { describe, it, expect } from 'vitest';
import { HalSystemStatus, HalValue, ValueChange } from '../hal';
import { ErrorMessage, FileInfo, LinuxCNCStatus, ReadFileResponse } from '../linuxcnc';

// Field numbers from the proto files.
const HAL_VALUE_S64_FIELD = 5;
const HAL_VALUE_U64_FIELD = 6;
const VARINT_WIRE_TYPE = 0;

function varintTag(fieldNumber: number): number {
  return (fieldNumber << 3) | VARINT_WIRE_TYPE;
}

function encodeSigned(fieldNumber: number, decimalValue: string): Uint8Array {
  return new BinaryWriter().uint32(varintTag(fieldNumber)).int64(decimalValue).finish();
}

function encodeUnsigned(fieldNumber: number, decimalValue: string): Uint8Array {
  return new BinaryWriter().uint32(varintTag(fieldNumber)).uint64(decimalValue).finish();
}

function decodeS64(decimalValue: string): number | undefined {
  return HalValue.decode(encodeSigned(HAL_VALUE_S64_FIELD, decimalValue)).s64Value;
}

function decodeU64(decimalValue: string): number | undefined {
  return HalValue.decode(encodeUnsigned(HAL_VALUE_U64_FIELD, decimalValue)).u64Value;
}

describe('HAL 64-bit values', () => {
  it('are exact up to Number.MAX_SAFE_INTEGER', () => {
    expect(decodeS64('9007199254740991')).toBe(Number.MAX_SAFE_INTEGER);
    expect(decodeS64('-9007199254740991')).toBe(Number.MIN_SAFE_INTEGER);
    expect(decodeU64('9007199254740991')).toBe(Number.MAX_SAFE_INTEGER);
  });

  it('just above 2^53 arrive as the nearest number, not divided by a million', () => {
    // 2^53 + 1 has no exact number; the nearest one is 2^53.
    expect(decodeS64('9007199254740993')).toBe(2 ** 53);
    expect(decodeS64('-9007199254740993')).toBe(-(2 ** 53));
    expect(decodeU64('9007199254740993')).toBe(2 ** 53);
    // 2^53 + 2 is a number itself.
    expect(decodeS64('9007199254740994')).toBe(9007199254740994);
    expect(decodeU64('9007199254740994')).toBe(9007199254740994);
  });

  it('well above 2^53 arrive unscaled', () => {
    expect(decodeS64('1152921504606846976')).toBe(2 ** 60);
    expect(decodeS64('-1152921504606846976')).toBe(-(2 ** 60));
    expect(decodeU64('1152921504606846976')).toBe(2 ** 60);
    expect(decodeU64('9223372036854775808')).toBe(2 ** 63);
  });

  it('at the ends of the 64-bit range arrive as the nearest number', () => {
    expect(decodeS64('-9223372036854775808')).toBe(-(2 ** 63));
    expect(decodeS64('9223372036854775807')).toBe(2 ** 63);
    expect(decodeU64('18446744073709551615')).toBe(2 ** 64);
  });

  it('inside another message are decoded the same way', () => {
    const change = ValueChange.decode(
      new BinaryWriter()
        .uint32((3 << 3) | 2)
        .bytes(encodeSigned(HAL_VALUE_S64_FIELD, '-1152921504606846976'))
        .uint32((4 << 3) | 2)
        .bytes(encodeUnsigned(HAL_VALUE_U64_FIELD, '1152921504606846976'))
        .finish()
    );

    expect(change.oldValue?.s64Value).toBe(-(2 ** 60));
    expect(change.newValue?.u64Value).toBe(2 ** 60);
  });

  it('are written to the wire unscaled', () => {
    const signedReader = new BinaryReader(HalValue.encode({ s64Value: -(2 ** 60) }).finish());
    expect(signedReader.uint32()).toBe(varintTag(HAL_VALUE_S64_FIELD));
    expect(signedReader.int64().toString()).toBe('-1152921504606846976');

    const unsignedReader = new BinaryReader(HalValue.encode({ u64Value: 2 ** 63 }).finish());
    expect(unsignedReader.uint32()).toBe(varintTag(HAL_VALUE_U64_FIELD));
    expect(unsignedReader.uint64().toString()).toBe('9223372036854775808');
  });

  it('survive an encode and decode round trip', () => {
    const largeValue = 2 ** 60;
    expect(HalValue.decode(HalValue.encode({ s64Value: largeValue }).finish()).s64Value).toBe(
      largeValue
    );
    expect(HalValue.decode(HalValue.encode({ u64Value: largeValue }).finish()).u64Value).toBe(
      largeValue
    );
  });

  it('pass through JSON unscaled', () => {
    expect(HalValue.fromJSON({ s64Value: 2 ** 60 }).s64Value).toBe(2 ** 60);
    expect(HalValue.toJSON({ u64Value: 2 ** 60 })).toEqual({ u64Value: 2 ** 60 });
  });
});

describe('other 64-bit fields that are not timestamps', () => {
  it('ErrorMessage.sequence arrives unscaled', () => {
    const message = ErrorMessage.decode(encodeSigned(4, '1152921504606846976'));
    expect(message.sequence).toBe(2 ** 60);
  });

  it('FileInfo.size_bytes arrives unscaled', () => {
    const fileInfo = FileInfo.decode(encodeSigned(3, '9007199254740993'));
    expect(fileInfo.sizeBytes).toBe(2 ** 53);
  });

  it('ReadFileResponse.offset and total_size_bytes arrive unscaled', () => {
    const response = ReadFileResponse.decode(
      new BinaryWriter()
        .uint32(varintTag(3))
        .int64('1152921504606846976')
        .uint32(varintTag(4))
        .int64('2305843009213693952')
        .finish()
    );
    expect(response.offset).toBe(2 ** 60);
    expect(response.totalSizeBytes).toBe(2 ** 61);
  });
});

describe('timestamps', () => {
  // 2023-11-14T22:13:20.123456789Z in nanoseconds since the epoch.
  const NANOSECOND_TIMESTAMP = '1700000000123456789';
  const SAME_TIME_IN_MILLISECONDS = 1700000000123;

  it('in nanoseconds arrive as milliseconds', () => {
    expect(HalSystemStatus.decode(encodeSigned(1, NANOSECOND_TIMESTAMP)).timestamp).toBe(
      SAME_TIME_IN_MILLISECONDS
    );
    expect(LinuxCNCStatus.decode(encodeSigned(1, NANOSECOND_TIMESTAMP)).timestamp).toBe(
      SAME_TIME_IN_MILLISECONDS
    );
    expect(ErrorMessage.decode(encodeSigned(3, NANOSECOND_TIMESTAMP)).timestamp).toBe(
      SAME_TIME_IN_MILLISECONDS
    );
    expect(ValueChange.decode(encodeSigned(1, NANOSECOND_TIMESTAMP)).timestamp).toBe(
      SAME_TIME_IN_MILLISECONDS
    );
  });

  it('make the date they stand for', () => {
    const status = HalSystemStatus.decode(encodeSigned(1, NANOSECOND_TIMESTAMP));
    expect(new Date(status.timestamp).toISOString()).toBe('2023-11-14T22:13:20.123Z');
  });

  it('of files arrive as milliseconds too', () => {
    expect(FileInfo.decode(encodeSigned(4, NANOSECOND_TIMESTAMP)).modifiedTimestamp).toBe(
      SAME_TIME_IN_MILLISECONDS
    );
    expect(ReadFileResponse.decode(encodeSigned(6, NANOSECOND_TIMESTAMP)).modifiedTimestamp).toBe(
      SAME_TIME_IN_MILLISECONDS
    );
  });

  it('small enough to be an exact number arrive unchanged', () => {
    expect(HalSystemStatus.decode(encodeSigned(1, '1700000000123')).timestamp).toBe(1700000000123);
    expect(HalSystemStatus.decode(encodeSigned(1, '9007199254740991')).timestamp).toBe(
      Number.MAX_SAFE_INTEGER
    );
  });

  it('before the epoch are scaled toward zero', () => {
    expect(HalSystemStatus.decode(encodeSigned(1, '-1700000000123456789')).timestamp).toBe(
      -SAME_TIME_IN_MILLISECONDS
    );
  });
});
