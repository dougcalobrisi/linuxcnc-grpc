/**
 * Upload File Example
 *
 * Uploads a G-code file to the LinuxCNC nc_files directory via gRPC,
 * lists files to confirm, and optionally cleans up.
 *
 * Usage:
 *   npx tsx upload_file.ts [--host HOST] [--port PORT] [--cleanup]
 */

import { Metadata } from "@grpc/grpc-js";
import { program } from "commander";
import {
  LinuxCNCServiceClient,
  UploadFileRequest,
  ListFilesRequest,
  DeleteFileRequest,
  credentials,
} from "linuxcnc-grpc";

program
  .option("--host <host>", "gRPC server host", "localhost")
  .option("--port <port>", "gRPC server port", "50051")
  .option("--cleanup", "Delete the file after uploading")
  .parse();

const opts = program.opts();
const address = `${opts.host}:${opts.port}`;

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

const filename = "grpc_example.ngc";
const deadline = new Date(Date.now() + 10000);

// Upload the file
console.log(`Uploading '${filename}'...`);
client.uploadFile(
  UploadFileRequest.create({ filename, content: sampleGCode }),
  new Metadata(),
  { deadline },
  (err, uploadResp) => {
    if (err) {
      console.error(`UploadFile error: ${err.code}: ${err.details}`);
      client.close();
      process.exit(1);
    }

    const overwriteMsg = uploadResp.overwritten ? " (overwritten)" : "";
    console.log(`  Written to: ${uploadResp.path}${overwriteMsg}`);
    console.log(`  Size: ${sampleGCode.length} bytes`);

    // List files to confirm
    console.log("\nListing files...");
    client.listFiles(
      ListFilesRequest.create({}),
      new Metadata(),
      { deadline },
      (err, listResp) => {
        if (err) {
          console.error(`ListFiles error: ${err.code}: ${err.details}`);
          client.close();
          process.exit(1);
        }

        console.log(`  Directory: ${listResp.directory}`);
        console.log(`  ${"Name".padEnd(30)} ${"Size".padStart(8)}  Type`);
        console.log(`  ${"-".repeat(30)} ${"-".repeat(8)}  ----`);
        for (const f of listResp.files) {
          const ftype = f.isDirectory ? "DIR" : "FILE";
          console.log(`  ${f.name.padEnd(30)} ${String(f.sizeBytes).padStart(8)}  ${ftype}`);
        }

        // Optionally clean up
        if (opts.cleanup) {
          console.log(`\nDeleting '${filename}'...`);
          client.deleteFile(
            DeleteFileRequest.create({ filename }),
            new Metadata(),
            { deadline },
            (err, deleteResp) => {
              if (err) {
                console.error(`DeleteFile error: ${err.code}: ${err.details}`);
                client.close();
                process.exit(1);
              }
              console.log(`  Deleted: ${deleteResp.path}`);
              client.close();
            }
          );
        } else {
          client.close();
        }
      }
    );
  }
);
