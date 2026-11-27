//! Upload File Example
//!
//! Uploads a G-code file to the LinuxCNC nc_files directory via gRPC,
//! lists files to confirm, and optionally cleans up.
//!
//! The upload never replaces a file: if the name is already taken the example
//! stops, and `--cleanup` only deletes a file this run created.
//!
//! # Usage
//!
//! ```bash
//! cargo run --bin upload_file -- --host localhost --port 50051 [--filename NAME] [--cleanup]
//! ```

use clap::Parser;
use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
use linuxcnc_grpc::linuxcnc::{DeleteFileRequest, ListFilesRequest, UploadFileRequest};
use std::time::Duration;
use tonic::transport::{Channel, Endpoint};

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

    /// Name to upload as
    #[arg(long, default_value = "grpc_example.ngc")]
    filename: String,

    /// Delete the uploaded file afterwards
    #[arg(long)]
    cleanup: bool,
}

const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const RPC_TIMEOUT: Duration = Duration::from_secs(10); // Deadline of every RPC

type FileClient = LinuxCncServiceClient<Channel>;

/// Build a request with the RPC deadline. The client enforces it and the
/// server sees it too.
fn with_deadline<T>(message: T) -> tonic::Request<T> {
    let mut request = tonic::Request::new(message);
    request.set_timeout(RPC_TIMEOUT);
    request
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

async fn list_files(client: &mut FileClient) -> Result<(), Box<dyn std::error::Error>> {
    println!("\nListing files...");
    let list_response = client
        .list_files(with_deadline(ListFilesRequest {
            subdirectory: String::new(),
        }))
        .await?
        .into_inner();

    println!("  Directory: {}", list_response.directory);
    println!("  {:<30} {:>8}  {}", "Name", "Size", "Type");
    println!(
        "  {:<30} {:>8}  {}",
        "-".repeat(30),
        "-".repeat(8),
        "----"
    );
    for file_info in &list_response.files {
        let file_type = if file_info.is_directory { "DIR" } else { "FILE" };
        println!(
            "  {:<30} {:>8}  {}",
            file_info.name, file_info.size_bytes, file_type
        );
    }
    Ok(())
}

async fn delete_file(
    client: &mut FileClient,
    filename: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    println!("\nDeleting '{}'...", filename);
    let delete_response = client
        .delete_file(with_deadline(DeleteFileRequest {
            filename: filename.to_string(),
        }))
        .await
        .map_err(|status| format!("could not delete '{filename}': {status}"))?
        .into_inner();
    println!("  Deleted: {}", delete_response.path);
    Ok(())
}

async fn run(args: Args) -> Result<(), Box<dyn std::error::Error>> {
    let addr = format!("http://{}:{}", args.host, args.port);
    let channel = Endpoint::from_shared(addr)?
        .connect_timeout(CONNECT_TIMEOUT)
        .connect()
        .await?;
    let mut client = LinuxCncServiceClient::new(channel);

    // Upload the file. fail_if_exists makes the server refuse to replace an
    // existing file instead of overwriting it.
    println!("Uploading '{}'...", args.filename);
    let upload_result = client
        .upload_file(with_deadline(UploadFileRequest {
            filename: args.filename.clone(),
            content: SAMPLE_GCODE.to_string(),
            fail_if_exists: true,
            ..Default::default()
        }))
        .await;
    let upload_response = match upload_result {
        Ok(response) => response.into_inner(),
        Err(status) if status.code() == tonic::Code::AlreadyExists => {
            return Err(format!(
                "'{}' already exists on the machine and was left untouched. Delete it there \
                 or pick another name with --filename.",
                args.filename
            )
            .into());
        }
        Err(status) => return Err(status.into()),
    };
    println!("  Written to: {}", upload_response.path);
    println!("  Size: {} bytes", SAMPLE_GCODE.len());

    // From here on this run has created the file, so --cleanup can delete it
    // without ever deleting a file that was there before. The delete runs
    // even when the listing failed.
    let list_result = list_files(&mut client).await;
    if args.cleanup {
        delete_file(&mut client, &args.filename).await?;
    }
    list_result
}

#[tokio::main]
async fn main() {
    if let Err(error) = run(Args::parse()).await {
        eprintln!("Error: {error}");
        std::process::exit(1);
    }
}
