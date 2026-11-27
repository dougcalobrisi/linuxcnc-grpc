// Upload File Example
//
// Uploads a G-code file to the LinuxCNC nc_files directory via gRPC,
// lists files to confirm, and optionally cleans up.
//
// The upload never replaces a file: if the name is already taken the example
// stops, and --cleanup only deletes a file this run created.
//
// Usage:
//
//	go run ./cmd/upload_file [--host HOST] [--port PORT] [--filename NAME] [--cleanup]
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

// rpcTimeout is the deadline of every RPC.
const rpcTimeout = 10 * time.Second

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

// rpcContext returns a context with a fresh deadline for one RPC.
func rpcContext() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), rpcTimeout)
}

func listFiles(client pb.LinuxCNCServiceClient) error {
	fmt.Println("\nListing files...")
	ctx, cancel := rpcContext()
	defer cancel()
	listResponse, err := client.ListFiles(ctx, &pb.ListFilesRequest{})
	if err != nil {
		return fmt.Errorf("ListFiles failed: %w", err)
	}
	fmt.Printf("  Directory: %s\n", listResponse.GetDirectory())
	fmt.Printf("  %-30s %8s  %s\n", "Name", "Size", "Type")
	fmt.Printf("  %-30s %8s  %s\n", "------------------------------", "--------", "----")
	for _, fileInfo := range listResponse.GetFiles() {
		fileType := "FILE"
		if fileInfo.GetIsDirectory() {
			fileType = "DIR"
		}
		fmt.Printf("  %-30s %8d  %s\n", fileInfo.GetName(), fileInfo.GetSizeBytes(), fileType)
	}
	return nil
}

func deleteFile(client pb.LinuxCNCServiceClient, filename string) error {
	fmt.Printf("\nDeleting '%s'...\n", filename)
	ctx, cancel := rpcContext()
	defer cancel()
	deleteResponse, err := client.DeleteFile(ctx, &pb.DeleteFileRequest{Filename: filename})
	if err != nil {
		return fmt.Errorf("could not delete '%s': %w", filename, err)
	}
	fmt.Printf("  Deleted: %s\n", deleteResponse.GetPath())
	return nil
}

func run(host string, port int, filename string, cleanup bool) error {
	addr := fmt.Sprintf("%s:%d", host, port)
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return fmt.Errorf("failed to connect: %w", err)
	}
	defer func() { _ = conn.Close() }()

	client := pb.NewLinuxCNCServiceClient(conn)

	// Upload the file. FailIfExists makes the server refuse to replace an
	// existing file instead of overwriting it.
	fmt.Printf("Uploading '%s'...\n", filename)
	uploadCtx, cancelUpload := rpcContext()
	defer cancelUpload()
	uploadResponse, err := client.UploadFile(uploadCtx, &pb.UploadFileRequest{
		Filename:     filename,
		Content:      sampleGCode,
		FailIfExists: true,
	})
	if status.Code(err) == codes.AlreadyExists {
		return fmt.Errorf("'%s' already exists on the machine and was left untouched. Delete it there or pick another name with --filename", filename)
	}
	if err != nil {
		return fmt.Errorf("UploadFile failed: %w", err)
	}
	fmt.Printf("  Written to: %s\n", uploadResponse.GetPath())
	fmt.Printf("  Size: %d bytes\n", len(sampleGCode))

	// From here on this run has created the file, so --cleanup can delete it
	// without ever deleting a file that was there before.
	listError := listFiles(client)
	if cleanup {
		return errors.Join(listError, deleteFile(client, filename))
	}
	return listError
}

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	filename := flag.String("filename", "grpc_example.ngc", "Name to upload as")
	cleanup := flag.Bool("cleanup", false, "Delete the uploaded file afterwards")
	flag.Parse()

	if err := run(*host, *port, *filename, *cleanup); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}
