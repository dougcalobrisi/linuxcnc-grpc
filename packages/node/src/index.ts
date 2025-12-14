/**
 * LinuxCNC gRPC client for TypeScript/JavaScript
 *
 * This package provides TypeScript types and gRPC client stubs for
 * communicating with a LinuxCNC gRPC server.
 *
 * @example
 * ```typescript
 * import { LinuxCNCServiceClient, GetStatusRequest } from 'linuxcnc-grpc';
 * import * as grpc from '@grpc/grpc-js';
 *
 * const client = new LinuxCNCServiceClient(
 *   'localhost:50051',
 *   grpc.credentials.createInsecure()
 * );
 *
 * client.getStatus(GetStatusRequest.create(), (err, status) => {
 *   if (err) {
 *     console.error('Error:', err);
 *     return;
 *   }
 *   console.log('Status:', status);
 * });
 * ```
 */

export * from './linuxcnc';
export * from './hal';
