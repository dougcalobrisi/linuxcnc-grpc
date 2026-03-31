module github.com/dougcalobrisi/linuxcnc-grpc/examples/go

go 1.24.0

require (
	github.com/dougcalobrisi/linuxcnc-grpc v0.5.0
	google.golang.org/grpc v1.79.3
)

require (
	golang.org/x/net v0.48.0 // indirect
	golang.org/x/sys v0.39.0 // indirect
	golang.org/x/text v0.32.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20251202230838-ff82c1b0f217 // indirect
	google.golang.org/protobuf v1.36.10 // indirect
)

replace github.com/dougcalobrisi/linuxcnc-grpc => ../..
