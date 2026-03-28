use std::env;
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let out_dir = PathBuf::from(env::var("OUT_DIR")?);

    // Proto files are included in the crate under proto/
    let proto_dir = "proto";

    for proto_file in &["linuxcnc.proto", "hal.proto"] {
        tonic_build::configure()
            .build_server(true)
            .build_client(true)
            .out_dir(&out_dir)
            .compile_protos(&[format!("{}/{}", proto_dir, proto_file)], &[proto_dir])?;
    }

    Ok(())
}
