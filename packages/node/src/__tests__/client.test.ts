/**
 * Integration tests for LinuxCNC gRPC TypeScript client.
 *
 * These tests connect to a Python mock server for cross-language validation.
 */

import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as grpc from '@grpc/grpc-js';
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import {
  LinuxCNCServiceClient,
  GetStatusRequest,
  LinuxCNCCommand,
  WaitCompleteRequest,
  StreamStatusRequest,
  TaskMode,
  TaskState,
  ExecState,
  RcsStatus,
  JogType,
} from '../linuxcnc';
import {
  HalServiceClient,
  GetSystemStatusRequest,
  QueryPinsCommand,
  QuerySignalsCommand,
  QueryParamsCommand,
  QueryComponentsCommand,
  GetValueCommand,
  HalStreamStatusRequest,
  HalType,
} from '../hal';

// Load fixtures
const fixturesPath = path.join(__dirname, '../../../../tests/fixtures');
const linuxcncFixture = JSON.parse(
  fs.readFileSync(path.join(fixturesPath, 'linuxcnc_status.json'), 'utf-8')
);
const halFixture = JSON.parse(
  fs.readFileSync(path.join(fixturesPath, 'hal_status.json'), 'utf-8')
);

// Test configuration
const MOCK_SERVER_PORT = 50097;
const MOCK_SERVER_ADDRESS = `localhost:${MOCK_SERVER_PORT}`;
const MOCK_SERVER_SCRIPT = path.join(__dirname, '../../../../tests/mock_server.py');

let mockServer: ChildProcess;
let linuxcncClient: LinuxCNCServiceClient;
let halClient: HalServiceClient;

// Helper to promisify gRPC client calls
function promisify<TReq, TRes>(
  fn: (req: TReq, callback: (err: grpc.ServiceError | null, res?: TRes) => void) => grpc.ClientUnaryCall
): (req: TReq) => Promise<TRes> {
  return (req: TReq) =>
    new Promise((resolve, reject) => {
      fn(req, (err, res) => {
        if (err) reject(err);
        else resolve(res!);
      });
    });
}

beforeAll(async () => {
  // Start mock server
  mockServer = spawn('python3', [MOCK_SERVER_SCRIPT, '--port', String(MOCK_SERVER_PORT)], {
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  // Wait for ready signal
  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Mock server startup timeout')), 10000);

    mockServer.stdout?.on('data', (data: Buffer) => {
      if (data.toString().includes('READY:')) {
        clearTimeout(timeout);
        resolve();
      }
    });

    mockServer.on('error', (err) => {
      clearTimeout(timeout);
      reject(err);
    });
  });

  // Create clients
  const credentials = grpc.credentials.createInsecure();
  linuxcncClient = new LinuxCNCServiceClient(MOCK_SERVER_ADDRESS, credentials);
  halClient = new HalServiceClient(MOCK_SERVER_ADDRESS, credentials);
});

afterAll(() => {
  // Close clients
  linuxcncClient?.close();
  halClient?.close();

  // Kill mock server
  if (mockServer) {
    mockServer.kill();
  }
});

// =============================================================================
// LinuxCNC Service Tests
// =============================================================================

describe('LinuxCNCService', () => {
  it('GetStatus returns expected mock data', async () => {
    const getStatus = promisify<GetStatusRequest, any>(
      linuxcncClient.getStatus.bind(linuxcncClient)
    );
    const status = await getStatus(GetStatusRequest.create());

    // Verify task status
    expect(status.task?.taskMode).toBe(TaskMode.MODE_MANUAL);
    expect(status.task?.taskState).toBe(TaskState.STATE_ON);
    expect(status.task?.execState).toBe(ExecState.EXEC_DONE);
    expect(status.task?.echoSerialNumber).toBe(linuxcncFixture.task.echo_serial_number);

    // Verify trajectory
    expect(status.trajectory?.joints).toBe(linuxcncFixture.trajectory.joints);
    expect(status.trajectory?.enabled).toBe(linuxcncFixture.trajectory.enabled);

    // Verify position
    expect(status.position?.actualPosition?.x).toBe(linuxcncFixture.position.actual_position.x);
    expect(status.position?.actualPosition?.y).toBe(linuxcncFixture.position.actual_position.y);
    expect(status.position?.actualPosition?.z).toBe(linuxcncFixture.position.actual_position.z);

    // Verify joints
    expect(status.joints).toHaveLength(3);
    status.joints?.forEach((joint: any, i: number) => {
      expect(joint.homed).toBe(linuxcncFixture.joints[i].homed);
    });

    // Verify tool
    expect(status.tool?.toolInSpindle).toBe(linuxcncFixture.tool.tool_in_spindle);
  });

  it('SendCommand with state command succeeds', async () => {
    const sendCommand = promisify<LinuxCNCCommand, any>(
      linuxcncClient.sendCommand.bind(linuxcncClient)
    );

    const command = LinuxCNCCommand.create({
      serial: 100,
      state: { state: TaskState.STATE_ON },
    });

    const response = await sendCommand(command);
    expect(response.serial).toBe(100);
    expect(response.status).toBe(RcsStatus.RCS_DONE);
  });

  it('SendCommand with MDI command succeeds', async () => {
    const sendCommand = promisify<LinuxCNCCommand, any>(
      linuxcncClient.sendCommand.bind(linuxcncClient)
    );

    const command = LinuxCNCCommand.create({
      serial: 101,
      mdi: { command: 'G0 X10 Y10' },
    });

    const response = await sendCommand(command);
    expect(response.serial).toBe(101);
    expect(response.status).toBe(RcsStatus.RCS_DONE);
  });

  it('SendCommand with jog command succeeds', async () => {
    const sendCommand = promisify<LinuxCNCCommand, any>(
      linuxcncClient.sendCommand.bind(linuxcncClient)
    );

    const command = LinuxCNCCommand.create({
      serial: 102,
      jog: {
        type: JogType.JOG_CONTINUOUS,
        isJoint: false,
        index: 0,
        velocity: 10.0,
      },
    });

    const response = await sendCommand(command);
    expect(response.serial).toBe(102);
    expect(response.status).toBe(RcsStatus.RCS_DONE);
  });

  it('WaitComplete returns immediately in mock', async () => {
    const waitComplete = promisify<WaitCompleteRequest, any>(
      linuxcncClient.waitComplete.bind(linuxcncClient)
    );

    const request = WaitCompleteRequest.create({
      serial: 50,
      timeout: 5.0,
    });

    const response = await waitComplete(request);
    expect(response.serial).toBe(50);
    expect(response.status).toBe(RcsStatus.RCS_DONE);
  });

  it('StreamStatus returns multiple updates', async () => {
    const stream = linuxcncClient.streamStatus(
      StreamStatusRequest.create({ interval: 0.05 })
    );

    const updates: any[] = [];
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        stream.cancel();
        resolve();
      }, 500);

      stream.on('data', (status: any) => {
        updates.push(status);
        if (updates.length >= 3) {
          clearTimeout(timeout);
          stream.cancel();
          resolve();
        }
      });

      stream.on('error', (err: any) => {
        if (err.code !== grpc.status.CANCELLED) {
          clearTimeout(timeout);
          reject(err);
        }
      });
    });

    expect(updates.length).toBeGreaterThanOrEqual(3);
    updates.forEach((status) => {
      expect(status.task?.taskMode).toBe(TaskMode.MODE_MANUAL);
    });
  });
});

// =============================================================================
// HAL Service Tests
// =============================================================================

describe('HalService', () => {
  it('GetSystemStatus returns expected mock data', async () => {
    const getSystemStatus = promisify<GetSystemStatusRequest, any>(
      halClient.getSystemStatus.bind(halClient)
    );
    const status = await getSystemStatus(GetSystemStatusRequest.create());

    // Verify system info
    expect(status.isSim).toBe(halFixture.is_sim);
    expect(status.isUserspace).toBe(halFixture.is_userspace);
    expect(status.kernelVersion).toBe(halFixture.kernel_version);

    // Verify pins
    expect(status.pins).toHaveLength(halFixture.pins.length);

    // Verify signals
    expect(status.signals).toHaveLength(halFixture.signals.length);

    // Verify components
    expect(status.components).toHaveLength(halFixture.components.length);
  });

  it('QueryPins with wildcard returns all pins', async () => {
    const queryPins = promisify<QueryPinsCommand, any>(
      halClient.queryPins.bind(halClient)
    );
    const response = await queryPins(QueryPinsCommand.create({ pattern: '*' }));

    expect(response.success).toBe(true);
    expect(response.pins).toHaveLength(3);
  });

  it('QueryPins with pattern filters correctly', async () => {
    const queryPins = promisify<QueryPinsCommand, any>(
      halClient.queryPins.bind(halClient)
    );
    const response = await queryPins(QueryPinsCommand.create({ pattern: 'axis.*' }));

    expect(response.success).toBe(true);
    expect(response.pins).toHaveLength(1);
    expect(response.pins[0].name).toBe('axis.x.pos-cmd');
  });

  it('QuerySignals returns all signals', async () => {
    const querySignals = promisify<QuerySignalsCommand, any>(
      halClient.querySignals.bind(halClient)
    );
    const response = await querySignals(QuerySignalsCommand.create({ pattern: '*' }));

    expect(response.success).toBe(true);
    expect(response.signals).toHaveLength(2);
  });

  it('QueryParams returns all params', async () => {
    const queryParams = promisify<QueryParamsCommand, any>(
      halClient.queryParams.bind(halClient)
    );
    const response = await queryParams(QueryParamsCommand.create({ pattern: '*' }));

    expect(response.success).toBe(true);
    expect(response.params).toHaveLength(3);
  });

  it('QueryComponents returns all components', async () => {
    const queryComponents = promisify<QueryComponentsCommand, any>(
      halClient.queryComponents.bind(halClient)
    );
    const response = await queryComponents(QueryComponentsCommand.create({ pattern: '*' }));

    expect(response.success).toBe(true);
    expect(response.components).toHaveLength(3);

    const names = response.components.map((c: any) => c.name);
    expect(names).toContain('axis');
    expect(names).toContain('spindle');
    expect(names).toContain('iocontrol');
  });

  it('GetValue returns correct value', async () => {
    const getValue = promisify<GetValueCommand, any>(
      halClient.getValue.bind(halClient)
    );
    const response = await getValue(
      GetValueCommand.create({ name: 'axis.x.pos-cmd' })
    );

    expect(response.success).toBe(true);
    expect(response.type).toBe(HalType.HAL_FLOAT);
    expect(response.value?.floatValue).toBe(123.456);
  });

  it('StreamStatus returns multiple updates', async () => {
    const stream = halClient.streamStatus(
      HalStreamStatusRequest.create({ interval: 0.05 })
    );

    const updates: any[] = [];
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        stream.cancel();
        resolve();
      }, 500);

      stream.on('data', (status: any) => {
        updates.push(status);
        if (updates.length >= 3) {
          clearTimeout(timeout);
          stream.cancel();
          resolve();
        }
      });

      stream.on('error', (err: any) => {
        if (err.code !== grpc.status.CANCELLED) {
          clearTimeout(timeout);
          reject(err);
        }
      });
    });

    expect(updates.length).toBeGreaterThanOrEqual(3);
    updates.forEach((status) => {
      expect(status.isSim).toBe(true);
    });
  });
});
