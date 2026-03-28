/**
 * LinuxCNC gRPC client for TypeScript/JavaScript
 *
 * This package provides TypeScript types and gRPC client stubs for
 * communicating with a LinuxCNC gRPC server.
 *
 * @example
 * ```typescript
 * import { LinuxCNCServiceClient, GetStatusRequest, credentials } from 'linuxcnc-grpc';
 *
 * const client = new LinuxCNCServiceClient(
 *   'localhost:50051',
 *   credentials.createInsecure()
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

import * as grpc from '@grpc/grpc-js';

// Re-export grpc utilities to avoid version conflicts
export const credentials = grpc.credentials;
export const Metadata = grpc.Metadata;
export const status = grpc.status;
export type { ServiceError } from '@grpc/grpc-js';

export * from './linuxcnc';
export * from './hal';
