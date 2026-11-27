module github.com/dougcalobrisi/linuxcnc-grpc/examples/go

go 1.25.0

require (
	github.com/dougcalobrisi/linuxcnc-grpc v0.5.0
	google.golang.org/grpc v1.83.2
)

require (
	golang.org/x/net v0.58.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
	golang.org/x/text v0.41.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260526163538-3dc84a4a5aaa // indirect
	google.golang.org/protobuf v1.36.11 // indirect
)

replace github.com/dougcalobrisi/linuxcnc-grpc => ../..
