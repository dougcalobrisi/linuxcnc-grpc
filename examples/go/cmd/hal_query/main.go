// HAL Query Example
//
// Query HAL (Hardware Abstraction Layer) pins, signals, and parameters.
// Useful for debugging HAL configurations and monitoring I/O.
//
// Usage:
//
//	go run hal_query.go pins "axis.*"
//	go run hal_query.go signals
//	go run hal_query.go components
//	go run hal_query.go watch "spindle.0.speed-out" "axis.x.pos-cmd"
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"sort"
	"strings"
	"syscall"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
)

func formatValue(value *pb.HalValue) string {
	if value == nil {
		return "?"
	}
	switch v := value.Value.(type) {
	case *pb.HalValue_BitValue:
		if v.BitValue {
			return "TRUE"
		}
		return "FALSE"
	case *pb.HalValue_FloatValue:
		return fmt.Sprintf("%.6g", v.FloatValue)
	case *pb.HalValue_S32Value:
		return fmt.Sprintf("%d", v.S32Value)
	case *pb.HalValue_U32Value:
		return fmt.Sprintf("%d", v.U32Value)
	case *pb.HalValue_S64Value:
		return fmt.Sprintf("%d", v.S64Value)
	case *pb.HalValue_U64Value:
		return fmt.Sprintf("%d", v.U64Value)
	default:
		return "?"
	}
}

func formatType(halType pb.HalType) string {
	return strings.Replace(halType.String(), "HAL_", "", 1)
}

func formatDirection(direction pb.PinDirection) string {
	s := direction.String()
	s = strings.Replace(s, "HAL_", "", 1)
	s = strings.Replace(s, "PIN_DIR_", "", 1)
	return s
}

func queryPins(client pb.HalServiceClient, pattern string) {
	response, err := client.QueryPins(context.Background(), &pb.QueryPinsCommand{Pattern: pattern})
	if err != nil {
		log.Fatalf("QueryPins failed: %v", err)
	}
	if !response.Success {
		log.Fatalf("Error: %s", response.Error)
	}

	fmt.Printf("Found %d pins matching '%s':\n\n", len(response.Pins), pattern)
	fmt.Printf("%-50s %-6s %-4s %-15s %s\n", "Name", "Type", "Dir", "Value", "Signal")
	fmt.Println(strings.Repeat("-", 90))

	// Sort pins by name
	pins := response.Pins
	sort.Slice(pins, func(i, j int) bool { return pins[i].Name < pins[j].Name })

	for _, pin := range pins {
		direction := formatDirection(pin.Direction)
		value := formatValue(pin.Value)
		pinType := formatType(pin.Type)
		signal := pin.Signal
		if signal == "" {
			signal = "-"
		}
		fmt.Printf("%-50s %-6s %-4s %-15s %s\n", pin.Name, pinType, direction, value, signal)
	}
}

func querySignals(client pb.HalServiceClient, pattern string) {
	response, err := client.QuerySignals(context.Background(), &pb.QuerySignalsCommand{Pattern: pattern})
	if err != nil {
		log.Fatalf("QuerySignals failed: %v", err)
	}
	if !response.Success {
		log.Fatalf("Error: %s", response.Error)
	}

	fmt.Printf("Found %d signals matching '%s':\n\n", len(response.Signals), pattern)
	fmt.Printf("%-40s %-6s %-15s %-30s %s\n", "Name", "Type", "Value", "Driver", "Readers")
	fmt.Println(strings.Repeat("-", 100))

	// Sort signals by name
	signals := response.Signals
	sort.Slice(signals, func(i, j int) bool { return signals[i].Name < signals[j].Name })

	for _, sig := range signals {
		value := formatValue(sig.Value)
		sigType := formatType(sig.Type)
		driver := sig.Driver
		if driver == "" {
			driver = "(none)"
		}
		readers := "-"
		if sig.ReaderCount > 0 {
			readers = fmt.Sprintf("%d readers", sig.ReaderCount)
		}
		fmt.Printf("%-40s %-6s %-15s %-30s %s\n", sig.Name, sigType, value, driver, readers)
	}
}

func queryParams(client pb.HalServiceClient, pattern string) {
	response, err := client.QueryParams(context.Background(), &pb.QueryParamsCommand{Pattern: pattern})
	if err != nil {
		log.Fatalf("QueryParams failed: %v", err)
	}
	if !response.Success {
		log.Fatalf("Error: %s", response.Error)
	}

	fmt.Printf("Found %d parameters matching '%s':\n\n", len(response.Params), pattern)
	fmt.Printf("%-50s %-6s %-4s %s\n", "Name", "Type", "Mode", "Value")
	fmt.Println(strings.Repeat("-", 80))

	// Sort params by name
	params := response.Params
	sort.Slice(params, func(i, j int) bool { return params[i].Name < params[j].Name })

	for _, param := range params {
		value := formatValue(param.Value)
		paramType := formatType(param.Type)
		mode := "RO"
		if param.Direction == pb.ParamDirection_HAL_RW {
			mode = "RW"
		}
		fmt.Printf("%-50s %-6s %-4s %s\n", param.Name, paramType, mode, value)
	}
}

func queryComponents(client pb.HalServiceClient, pattern string) {
	response, err := client.QueryComponents(context.Background(), &pb.QueryComponentsCommand{Pattern: pattern})
	if err != nil {
		log.Fatalf("QueryComponents failed: %v", err)
	}
	if !response.Success {
		log.Fatalf("Error: %s", response.Error)
	}

	fmt.Printf("Found %d components matching '%s':\n\n", len(response.Components), pattern)
	fmt.Printf("%-30s %-6s %-6s %-6s %s\n", "Name", "ID", "Ready", "Pins", "Params")
	fmt.Println(strings.Repeat("-", 60))

	// Sort components by name
	comps := response.Components
	sort.Slice(comps, func(i, j int) bool { return comps[i].Name < comps[j].Name })

	for _, comp := range comps {
		ready := "No"
		if comp.Ready {
			ready = "Yes"
		}
		fmt.Printf("%-30s %-6d %-6s %-6d %d\n", comp.Name, comp.Id, ready, len(comp.Pins), len(comp.Params))
	}
}

func watchValues(client pb.HalServiceClient, names []string, intervalMs int) {
	// Set up cancellation on Ctrl+C
	ctx, cancel := context.WithCancel(context.Background())
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigChan
		cancel()
	}()

	stream, err := client.WatchValues(ctx, &pb.WatchRequest{
		Names:      names,
		IntervalMs: int32(intervalMs),
	})
	if err != nil {
		log.Fatalf("WatchValues failed: %v", err)
	}

	fmt.Printf("Watching %d values (interval: %dms)\n", len(names), intervalMs)
	fmt.Println("Press Ctrl+C to stop\n")

	for {
		batch, err := stream.Recv()
		if err == io.EOF {
			break
		}
		if err != nil {
			if ctx.Err() != nil {
				// Cancelled, normal exit
				break
			}
			log.Fatalf("Stream error: %v", err)
		}

		for _, change := range batch.Changes {
			oldVal := formatValue(change.OldValue)
			newVal := formatValue(change.NewValue)
			ts := time.Unix(0, change.Timestamp).Format("15:04:05")
			fmt.Printf("[%s] %s: %s -> %s\n", ts, change.Name, oldVal, newVal)
		}
	}
}

func getSystemStatus(client pb.HalServiceClient) {
	status, err := client.GetSystemStatus(context.Background(), &pb.GetSystemStatusRequest{})
	if err != nil {
		log.Fatalf("GetSystemStatus failed: %v", err)
	}

	fmt.Println("HAL System Status")
	fmt.Println(strings.Repeat("=", 40))
	fmt.Printf("Pins:       %d\n", len(status.Pins))
	fmt.Printf("Signals:    %d\n", len(status.Signals))
	fmt.Printf("Parameters: %d\n", len(status.Params))
	fmt.Printf("Components: %d\n", len(status.Components))
	fmt.Printf("Simulation: %v\n", status.IsSim)
	fmt.Printf("Real-time:  %v\n", status.IsRt)
	fmt.Printf("Userspace:  %v\n", status.IsUserspace)
	if status.KernelVersion != "" {
		fmt.Printf("Kernel:     %s\n", status.KernelVersion)
	}
}

func printUsage() {
	fmt.Println("Usage: go run hal_query.go <command> [options]")
	fmt.Println("\nCommands:")
	fmt.Println("  pins [pattern]       Query HAL pins")
	fmt.Println("  signals [pattern]    Query HAL signals")
	fmt.Println("  params [pattern]     Query HAL parameters")
	fmt.Println("  components [pattern] Query HAL components")
	fmt.Println("  watch <names...>     Watch values for changes")
	fmt.Println("  status               Get HAL system status")
	fmt.Println("\nExamples:")
	fmt.Println("  go run hal_query.go pins \"axis.*\"")
	fmt.Println("  go run hal_query.go signals")
	fmt.Println("  go run hal_query.go components")
	fmt.Println("  go run hal_query.go watch spindle.0.speed-out axis.x.pos-cmd")
}

func main() {
	host := flag.String("host", "localhost", "gRPC server host")
	port := flag.Int("port", 50051, "gRPC server port")
	intervalMs := flag.Int("interval", 500, "Update interval in ms for watch command")
	flag.Parse()

	args := flag.Args()
	if len(args) == 0 {
		printUsage()
		os.Exit(1)
	}

	command := args[0]

	addr := fmt.Sprintf("%s:%d", *host, *port)
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	defer conn.Close()

	client := pb.NewHalServiceClient(conn)

	switch command {
	case "pins":
		pattern := "*"
		if len(args) > 1 {
			pattern = args[1]
		}
		queryPins(client, pattern)
	case "signals":
		pattern := "*"
		if len(args) > 1 {
			pattern = args[1]
		}
		querySignals(client, pattern)
	case "params":
		pattern := "*"
		if len(args) > 1 {
			pattern = args[1]
		}
		queryParams(client, pattern)
	case "components":
		pattern := "*"
		if len(args) > 1 {
			pattern = args[1]
		}
		queryComponents(client, pattern)
	case "watch":
		if len(args) < 2 {
			fmt.Println("Error: watch command requires at least one name")
			os.Exit(1)
		}
		watchValues(client, args[1:], *intervalMs)
	case "status":
		getSystemStatus(client)
	default:
		fmt.Printf("Unknown command: %s\n\n", command)
		printUsage()
		os.Exit(1)
	}
}
