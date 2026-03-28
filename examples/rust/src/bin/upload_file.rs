//! Upload File Example
//!
//! Uploads a G-code file to the LinuxCNC nc_files directory via gRPC,
//! lists files to confirm, and optionally cleans up.
//!
//! # Usage
//!
//! ```bash
//! cargo run --bin upload_file -- --host localhost --port 50051 [--cleanup]
//! ```

use clap::Parser;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{DeleteFileRequest, ListFilesRequest, UploadFileRequest};

#[derive(Parser, Debug)]
#[command(name = "upload_file")]
#[command(about = "Upload a G-code file to LinuxCNC via gRPC")]
struct Args {
    /// gRPC server host
    #[arg(long, default_value = "localhost")]
    host: String,

    /// gRPC server port
    #[arg(long, default_value_t = 50051)]
    port: u16,

    /// Delete the file after uploading
    #[arg(long)]
    cleanup: bool,
}

const SAMPLE_GCODE: &str = "\
(Sample G-code uploaded via gRPC)
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
";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let addr = format!("http://{}:{}", args.host, args.port);
    let filename = "grpc_example.ngc".to_string();

    let mut client = LinuxCncServiceClient::connect(addr).await?;

    // Upload the file
    println!("Uploading '{}'...", filename);
    let upload_resp = client
        .upload_file(tonic::Request::new(UploadFileRequest {
            filename: filename.clone(),
            content: SAMPLE_GCODE.to_string(),
            fail_if_exists: false,
        }))
        .await?
        .into_inner();

    let overwrite_msg = if upload_resp.overwritten {
        " (overwritten)"
    } else {
        ""
    };
    println!("  Written to: {}{}", upload_resp.path, overwrite_msg);
    println!("  Size: {} bytes", SAMPLE_GCODE.len());

    // List files to confirm
    println!("\nListing files...");
    let list_resp = client
        .list_files(tonic::Request::new(ListFilesRequest {
            subdirectory: String::new(),
        }))
        .await?
        .into_inner();

    println!("  Directory: {}", list_resp.directory);
    println!("  {:<30} {:>8}  {}", "Name", "Size", "Type");
    println!(
        "  {:<30} {:>8}  {}",
        "-".repeat(30),
        "-".repeat(8),
        "----"
    );
    for f in &list_resp.files {
        let ftype = if f.is_directory { "DIR" } else { "FILE" };
        println!("  {:<30} {:>8}  {}", f.name, f.size_bytes, ftype);
    }

    // Optionally clean up
    if args.cleanup {
        println!("\nDeleting '{}'...", filename);
        let delete_resp = client
            .delete_file(tonic::Request::new(DeleteFileRequest {
                filename: filename.clone(),
            }))
            .await?
            .into_inner();
        println!("  Deleted: {}", delete_resp.path);
    }

    Ok(())
}
