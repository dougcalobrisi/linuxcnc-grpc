//! # LinuxCNC gRPC Client
//!
//! This crate provides Rust types and gRPC client stubs for communicating
//! with a LinuxCNC gRPC server.
//!
//! ## Example
//!
//! ```rust,no_run
//! use linuxcnc_grpc::linuxcnc::linux_cnc_service_client::LinuxCncServiceClient;
//! use linuxcnc_grpc::linuxcnc::GetStatusRequest;
//!
//! #[tokio::main]
//! async fn main() -> Result<(), Box<dyn std::error::Error>> {
//!     let mut client = LinuxCncServiceClient::connect("http://localhost:50051").await?;
//!
//!     let request = tonic::Request::new(GetStatusRequest {});
//!     let response = client.get_status(request).await?;
//!
//!     println!("Status: {:?}", response);
//!     Ok(())
//! }
//! ```

/// LinuxCNC machine control types and service definitions
pub mod linuxcnc {
    // linuxcnc.rs already includes linuxcnc.tonic.rs at the end
    include!("linuxcnc/linuxcnc.rs");
}

/// HAL (Hardware Abstraction Layer) types and service definitions
pub mod hal {
    // hal.rs already includes hal.tonic.rs at the end
    include!("hal/hal.rs");
}

// Re-export commonly used types at crate root
pub use linuxcnc::*;
