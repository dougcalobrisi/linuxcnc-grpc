/**
 * Upload File Example
 *
 * Uploads a G-code file to the LinuxCNC nc_files directory via gRPC,
 * lists files to confirm, and optionally cleans up.
 *
 * The upload never replaces a file: if the name is already taken the example
 * stops, and --cleanup only deletes a file this run created.
 *
 * Usage:
 *   npx tsx upload_file.ts [--host HOST] [--port PORT] [--filename NAME] [--cleanup]
 */

import { program } from "commander";
import {
  LinuxCNCServiceClient,
  UploadFileRequest,
  ListFilesRequest,
  DeleteFileRequest,
  UploadFileResponse,
  ListFilesResponse,
  DeleteFileResponse,
  Metadata,
  credentials,
  status,
} from "linuxcnc-grpc";
import type { ServiceError } from "linuxcnc-grpc";

program
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option("--filename <name>", "Name to upload as", "grpc_example.ngc")
  .option("--cleanup", "Delete the uploaded file afterwards")
  .parse();

const opts = program.opts();
const address = `${opts.host}:${opts.port}`;
const filename: string = opts.filename;

const RPC_TIMEOUT_MS = 10000; // Deadline of every RPC

const sampleGCode = `(Sample G-code uploaded via gRPC)
G21 (metric)
G90 (absolute positioning)
G0 Z5
G0 X0 Y0
G1 Z-1 F100
G1 X50 F200
G1 Y50
G1 X0
G1 Y0
G0 Z5
M2
`;

const client = new LinuxCNCServiceClient(
  address,
  credentials.createInsecure(),
  {
    'grpc.initial_reconnect_backoff_ms': 1000,
    'grpc.max_reconnect_backoff_ms': 5000,
  }
);

function deadlineIn(milliseconds: number): { deadline: Date } {
  return { deadline: new Date(Date.now() + milliseconds) };
}

function uploadFile(request: UploadFileRequest): Promise<UploadFileResponse> {
  return new Promise((resolve, reject) => {
    client.uploadFile(request, new Metadata(), deadlineIn(RPC_TIMEOUT_MS), (err, response) => {
      if (err) reject(err);
      else resolve(response);
    });
  });
}

function listFiles(): Promise<ListFilesResponse> {
  return new Promise((resolve, reject) => {
    client.listFiles(ListFilesRequest.create({}), new Metadata(), deadlineIn(RPC_TIMEOUT_MS), (err, response) => {
      if (err) reject(err);
      else resolve(response);
    });
  });
}

function deleteFile(request: DeleteFileRequest): Promise<DeleteFileResponse> {
  return new Promise((resolve, reject) => {
    client.deleteFile(request, new Metadata(), deadlineIn(RPC_TIMEOUT_MS), (err, response) => {
      if (err) reject(err);
      else resolve(response);
    });
  });
}

function errorText(err: unknown): string {
  const serviceError = err as Partial<ServiceError>;
  if (serviceError.code !== undefined) {
    return `gRPC error ${serviceError.code}: ${serviceError.details}`;
  }
  return err instanceof Error ? err.message : String(err);
}

async function main(): Promise<void> {
  // True only once this run has created the file, so that --cleanup can
  // never delete a file that was there before.
  let fileWasCreated = false;

  try {
    // Upload the file. failIfExists makes the server refuse to replace an
    // existing file instead of overwriting it.
    console.log(`Uploading '${filename}'...`);
    let uploadResponse: UploadFileResponse;
    try {
      uploadResponse = await uploadFile(
        UploadFileRequest.create({ filename, content: sampleGCode, failIfExists: true })
      );
    } catch (err) {
      if ((err as Partial<ServiceError>).code === status.ALREADY_EXISTS) {
        throw new Error(
          `'${filename}' already exists on the machine and was left untouched. ` +
            "Delete it there or pick another name with --filename."
        );
      }
      throw err;
    }
    fileWasCreated = true;
    console.log(`  Written to: ${uploadResponse.path}`);
    console.log(`  Size: ${sampleGCode.length} bytes`);

    // List files to confirm
    console.log("\nListing files...");
    const listResponse = await listFiles();
    console.log(`  Directory: ${listResponse.directory}`);
    console.log(`  ${"Name".padEnd(30)} ${"Size".padStart(8)}  Type`);
    console.log(`  ${"-".repeat(30)} ${"-".repeat(8)}  ----`);
    for (const fileInfo of listResponse.files) {
      const fileType = fileInfo.isDirectory ? "DIR" : "FILE";
      console.log(`  ${fileInfo.name.padEnd(30)} ${String(fileInfo.sizeBytes).padStart(8)}  ${fileType}`);
    }
  } catch (err) {
    console.error(`Error: ${errorText(err)}`);
    process.exitCode = 1;
  }

  try {
    // Optionally clean up, but only the file this run created.
    if (opts.cleanup && fileWasCreated) {
      console.log(`\nDeleting '${filename}'...`);
      const deleteResponse = await deleteFile(DeleteFileRequest.create({ filename }));
      console.log(`  Deleted: ${deleteResponse.path}`);
    }
  } catch (err) {
    console.error(`Could not delete '${filename}': ${errorText(err)}`);
    process.exitCode = 1;
  } finally {
    client.close();
  }
}

main();
