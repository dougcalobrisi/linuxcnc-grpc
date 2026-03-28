// Upload File Example
//
// Uploads a G-code file to the LinuxCNC nc_files directory via gRPC,
// lists files to confirm, and optionally cleans up.
//
// Usage:
//
//	go run main.go [--host HOST] [--port PORT] [--cleanup]
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

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
`

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	cleanup := flag.Bool("cleanup", false, "Delete the file after uploading")
	flag.Parse()

	addr := fmt.Sprintf("%s:%d", *host, *port)
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	defer conn.Close()

	client := pb.NewLinuxCNCServiceClient(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	filename := "grpc_example.ngc"

	// Upload the file
	fmt.Printf("Uploading '%s'...\n", filename)
	uploadResp, err := client.UploadFile(ctx, &pb.UploadFileRequest{
		Filename: filename,
		Content:  sampleGCode,
	})
	if err != nil {
		log.Fatalf("UploadFile failed: %v", err)
	}
	overwriteMsg := ""
	if uploadResp.Overwritten {
		overwriteMsg = " (overwritten)"
	}
	fmt.Printf("  Written to: %s%s\n", uploadResp.Path, overwriteMsg)
	fmt.Printf("  Size: %d bytes\n", len(sampleGCode))

	// List files to confirm
	fmt.Println("\nListing files...")
	listResp, err := client.ListFiles(ctx, &pb.ListFilesRequest{})
	if err != nil {
		log.Fatalf("ListFiles failed: %v", err)
	}
	fmt.Printf("  Directory: %s\n", listResp.Directory)
	fmt.Printf("  %-30s %8s  %s\n", "Name", "Size", "Type")
	fmt.Printf("  %-30s %8s  %s\n", "------------------------------", "--------", "----")
	for _, f := range listResp.Files {
		ftype := "FILE"
		if f.IsDirectory {
			ftype = "DIR"
		}
		fmt.Printf("  %-30s %8d  %s\n", f.Name, f.SizeBytes, ftype)
	}

	// Optionally clean up
	if *cleanup {
		fmt.Printf("\nDeleting '%s'...\n", filename)
		deleteResp, err := client.DeleteFile(ctx, &pb.DeleteFileRequest{
			Filename: filename,
		})
		if err != nil {
			log.Fatalf("DeleteFile failed: %v", err)
		}
		fmt.Printf("  Deleted: %s\n", deleteResp.Path)
	}
}
