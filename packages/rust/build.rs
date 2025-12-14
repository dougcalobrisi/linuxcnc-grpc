use std::env;
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let out_dir = PathBuf::from(env::var("OUT_DIR")?);

    // Configure tonic-build for LinuxCNC proto
    tonic_build::configure()
        .build_server(true)
        .build_client(true)
        .out_dir(&out_dir)
        .compile_protos(&["../../proto/linuxcnc.proto"], &["../../proto"])?;

    // Configure tonic-build for HAL proto
    tonic_build::configure()
        .build_server(true)
        .build_client(true)
        .out_dir(&out_dir)
        .compile_protos(&["../../proto/hal.proto"], &["../../proto"])?;

    Ok(())
}
