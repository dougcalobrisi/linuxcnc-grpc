// Package linuxcnc provides a gRPC client for LinuxCNC machine control and HAL.
//
// This package re-exports types from the generated protobuf code in packages/go
// for convenient access. Users can import this package directly:
//
//	import linuxcnc "github.com/dougcalobrisi/linuxcnc-grpc"
//
// Or import the generated types directly:
//
//	import "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
//
// # Quick Start
//
//	client, err := linuxcnc.NewClient("localhost:50051")
//	if err != nil {
//	    log.Fatal(err)
//	}
//	defer client.Close()
//
//	status, err := client.GetStatus(context.Background())
//	if err != nil {
//	    log.Fatal(err)
//	}
//	fmt.Printf("Mode: %v\n", status.Task.TaskMode)
package linuxcnc

import (
	"context"

	pb "github.com/dougcalobrisi/linuxcnc-grpc/packages/go"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// =============================================================================
// ENUM TYPES
// =============================================================================

type (
	// LinuxCNC enums
	InterpState    = pb.InterpState
	TaskMode       = pb.TaskMode
	TaskState      = pb.TaskState
	ExecState      = pb.ExecState
	RcsStatus      = pb.RcsStatus
	TrajMode       = pb.TrajMode
	MotionType     = pb.MotionType
	KinematicsType = pb.KinematicsType
	JointType      = pb.JointType
	SpindleDirection = pb.SpindleDirection
	SpindleCommand   = pb.SpindleCommand
	CoolantState     = pb.CoolantState
	BrakeState       = pb.BrakeState
	JogType          = pb.JogType
	AutoCommandType  = pb.AutoCommandType

	// Nested enum types
	ErrorMessageErrorType            = pb.ErrorMessage_ErrorType
	OperatorMessageCommandMessageType = pb.OperatorMessageCommand_MessageType

	// HAL enums
	HalType        = pb.HalType
	PinDirection   = pb.PinDirection
	ParamDirection = pb.ParamDirection
	MessageLevel   = pb.MessageLevel
)

// =============================================================================
// ENUM VALUES - LinuxCNC
// =============================================================================

const (
	// InterpState values
	InterpState_INTERP_STATE_UNSPECIFIED = pb.InterpState_INTERP_STATE_UNSPECIFIED
	InterpState_INTERP_IDLE              = pb.InterpState_INTERP_IDLE
	InterpState_INTERP_READING           = pb.InterpState_INTERP_READING
	InterpState_INTERP_PAUSED            = pb.InterpState_INTERP_PAUSED
	InterpState_INTERP_WAITING           = pb.InterpState_INTERP_WAITING

	// TaskMode values
	TaskMode_TASK_MODE_UNSPECIFIED = pb.TaskMode_TASK_MODE_UNSPECIFIED
	TaskMode_MODE_MANUAL           = pb.TaskMode_MODE_MANUAL
	TaskMode_MODE_AUTO             = pb.TaskMode_MODE_AUTO
	TaskMode_MODE_MDI              = pb.TaskMode_MODE_MDI

	// TaskState values
	TaskState_TASK_STATE_UNSPECIFIED = pb.TaskState_TASK_STATE_UNSPECIFIED
	TaskState_STATE_ESTOP            = pb.TaskState_STATE_ESTOP
	TaskState_STATE_ESTOP_RESET      = pb.TaskState_STATE_ESTOP_RESET
	TaskState_STATE_ON               = pb.TaskState_STATE_ON
	TaskState_STATE_OFF              = pb.TaskState_STATE_OFF

	// ExecState values
	ExecState_EXEC_STATE_UNSPECIFIED            = pb.ExecState_EXEC_STATE_UNSPECIFIED
	ExecState_EXEC_ERROR                        = pb.ExecState_EXEC_ERROR
	ExecState_EXEC_DONE                         = pb.ExecState_EXEC_DONE
	ExecState_EXEC_WAITING_FOR_MOTION           = pb.ExecState_EXEC_WAITING_FOR_MOTION
	ExecState_EXEC_WAITING_FOR_MOTION_QUEUE     = pb.ExecState_EXEC_WAITING_FOR_MOTION_QUEUE
	ExecState_EXEC_WAITING_FOR_IO               = pb.ExecState_EXEC_WAITING_FOR_IO
	ExecState_EXEC_WAITING_FOR_MOTION_AND_IO    = pb.ExecState_EXEC_WAITING_FOR_MOTION_AND_IO
	ExecState_EXEC_WAITING_FOR_DELAY            = pb.ExecState_EXEC_WAITING_FOR_DELAY
	ExecState_EXEC_WAITING_FOR_SYSTEM_CMD       = pb.ExecState_EXEC_WAITING_FOR_SYSTEM_CMD
	ExecState_EXEC_WAITING_FOR_SPINDLE_ORIENTED = pb.ExecState_EXEC_WAITING_FOR_SPINDLE_ORIENTED

	// RcsStatus values
	RcsStatus_RCS_STATUS_UNSPECIFIED = pb.RcsStatus_RCS_STATUS_UNSPECIFIED
	RcsStatus_RCS_DONE               = pb.RcsStatus_RCS_DONE
	RcsStatus_RCS_EXEC               = pb.RcsStatus_RCS_EXEC
	RcsStatus_RCS_ERROR              = pb.RcsStatus_RCS_ERROR

	// TrajMode values
	TrajMode_TRAJ_MODE_UNSPECIFIED = pb.TrajMode_TRAJ_MODE_UNSPECIFIED
	TrajMode_TRAJ_MODE_FREE        = pb.TrajMode_TRAJ_MODE_FREE
	TrajMode_TRAJ_MODE_COORD       = pb.TrajMode_TRAJ_MODE_COORD
	TrajMode_TRAJ_MODE_TELEOP      = pb.TrajMode_TRAJ_MODE_TELEOP

	// MotionType values
	MotionType_MOTION_TYPE_NONE        = pb.MotionType_MOTION_TYPE_NONE
	MotionType_MOTION_TYPE_TRAVERSE    = pb.MotionType_MOTION_TYPE_TRAVERSE
	MotionType_MOTION_TYPE_FEED        = pb.MotionType_MOTION_TYPE_FEED
	MotionType_MOTION_TYPE_ARC         = pb.MotionType_MOTION_TYPE_ARC
	MotionType_MOTION_TYPE_TOOLCHANGE  = pb.MotionType_MOTION_TYPE_TOOLCHANGE
	MotionType_MOTION_TYPE_PROBING     = pb.MotionType_MOTION_TYPE_PROBING
	MotionType_MOTION_TYPE_INDEXROTARY = pb.MotionType_MOTION_TYPE_INDEXROTARY

	// KinematicsType values
	KinematicsType_KINEMATICS_UNSPECIFIED  = pb.KinematicsType_KINEMATICS_UNSPECIFIED
	KinematicsType_KINEMATICS_IDENTITY     = pb.KinematicsType_KINEMATICS_IDENTITY
	KinematicsType_KINEMATICS_FORWARD_ONLY = pb.KinematicsType_KINEMATICS_FORWARD_ONLY
	KinematicsType_KINEMATICS_INVERSE_ONLY = pb.KinematicsType_KINEMATICS_INVERSE_ONLY
	KinematicsType_KINEMATICS_BOTH         = pb.KinematicsType_KINEMATICS_BOTH

	// JointType values
	JointType_JOINT_TYPE_UNSPECIFIED = pb.JointType_JOINT_TYPE_UNSPECIFIED
	JointType_JOINT_LINEAR           = pb.JointType_JOINT_LINEAR
	JointType_JOINT_ANGULAR          = pb.JointType_JOINT_ANGULAR

	// SpindleDirection values
	SpindleDirection_SPINDLE_STOPPED = pb.SpindleDirection_SPINDLE_STOPPED
	SpindleDirection_SPINDLE_FORWARD = pb.SpindleDirection_SPINDLE_FORWARD
	SpindleDirection_SPINDLE_REVERSE = pb.SpindleDirection_SPINDLE_REVERSE

	// SpindleCommand values
	SpindleCommand_SPINDLE_CMD_OFF      = pb.SpindleCommand_SPINDLE_CMD_OFF
	SpindleCommand_SPINDLE_CMD_FORWARD  = pb.SpindleCommand_SPINDLE_CMD_FORWARD
	SpindleCommand_SPINDLE_CMD_REVERSE  = pb.SpindleCommand_SPINDLE_CMD_REVERSE
	SpindleCommand_SPINDLE_CMD_INCREASE = pb.SpindleCommand_SPINDLE_CMD_INCREASE
	SpindleCommand_SPINDLE_CMD_DECREASE = pb.SpindleCommand_SPINDLE_CMD_DECREASE
	SpindleCommand_SPINDLE_CMD_CONSTANT = pb.SpindleCommand_SPINDLE_CMD_CONSTANT

	// CoolantState values
	CoolantState_COOLANT_OFF = pb.CoolantState_COOLANT_OFF
	CoolantState_COOLANT_ON  = pb.CoolantState_COOLANT_ON

	// BrakeState values
	BrakeState_BRAKE_RELEASE = pb.BrakeState_BRAKE_RELEASE
	BrakeState_BRAKE_ENGAGE  = pb.BrakeState_BRAKE_ENGAGE

	// JogType values
	JogType_JOG_STOP       = pb.JogType_JOG_STOP
	JogType_JOG_CONTINUOUS = pb.JogType_JOG_CONTINUOUS
	JogType_JOG_INCREMENT  = pb.JogType_JOG_INCREMENT

	// AutoCommandType values
	AutoCommandType_AUTO_RUN     = pb.AutoCommandType_AUTO_RUN
	AutoCommandType_AUTO_PAUSE   = pb.AutoCommandType_AUTO_PAUSE
	AutoCommandType_AUTO_RESUME  = pb.AutoCommandType_AUTO_RESUME
	AutoCommandType_AUTO_STEP    = pb.AutoCommandType_AUTO_STEP
	AutoCommandType_AUTO_REVERSE = pb.AutoCommandType_AUTO_REVERSE
	AutoCommandType_AUTO_FORWARD = pb.AutoCommandType_AUTO_FORWARD

	// ErrorMessage_ErrorType values
	ErrorMessage_ERROR_TYPE_UNSPECIFIED = pb.ErrorMessage_ERROR_TYPE_UNSPECIFIED
	ErrorMessage_OPERATOR_ERROR         = pb.ErrorMessage_OPERATOR_ERROR
	ErrorMessage_OPERATOR_TEXT          = pb.ErrorMessage_OPERATOR_TEXT
	ErrorMessage_OPERATOR_DISPLAY       = pb.ErrorMessage_OPERATOR_DISPLAY
	ErrorMessage_NML_ERROR              = pb.ErrorMessage_NML_ERROR
	ErrorMessage_NML_TEXT               = pb.ErrorMessage_NML_TEXT
	ErrorMessage_NML_DISPLAY            = pb.ErrorMessage_NML_DISPLAY

	// OperatorMessageCommand_MessageType values
	OperatorMessageCommand_ERROR   = pb.OperatorMessageCommand_ERROR
	OperatorMessageCommand_TEXT    = pb.OperatorMessageCommand_TEXT
	OperatorMessageCommand_DISPLAY = pb.OperatorMessageCommand_DISPLAY
)

// =============================================================================
// ENUM VALUES - HAL
// =============================================================================

const (
	// HalType values
	HalType_HAL_TYPE_UNSPECIFIED = pb.HalType_HAL_TYPE_UNSPECIFIED
	HalType_HAL_BIT              = pb.HalType_HAL_BIT
	HalType_HAL_FLOAT            = pb.HalType_HAL_FLOAT
	HalType_HAL_S32              = pb.HalType_HAL_S32
	HalType_HAL_U32              = pb.HalType_HAL_U32
	HalType_HAL_S64              = pb.HalType_HAL_S64
	HalType_HAL_U64              = pb.HalType_HAL_U64
	HalType_HAL_PORT             = pb.HalType_HAL_PORT

	// PinDirection values
	PinDirection_PIN_DIR_UNSPECIFIED = pb.PinDirection_PIN_DIR_UNSPECIFIED
	PinDirection_HAL_IN              = pb.PinDirection_HAL_IN
	PinDirection_HAL_OUT             = pb.PinDirection_HAL_OUT
	PinDirection_HAL_IO              = pb.PinDirection_HAL_IO

	// ParamDirection values
	ParamDirection_PARAM_DIR_UNSPECIFIED = pb.ParamDirection_PARAM_DIR_UNSPECIFIED
	ParamDirection_HAL_RO                = pb.ParamDirection_HAL_RO
	ParamDirection_HAL_RW                = pb.ParamDirection_HAL_RW

	// MessageLevel values
	MessageLevel_MSG_LEVEL_UNSPECIFIED = pb.MessageLevel_MSG_LEVEL_UNSPECIFIED
	MessageLevel_MSG_NONE              = pb.MessageLevel_MSG_NONE
	MessageLevel_MSG_ERR               = pb.MessageLevel_MSG_ERR
	MessageLevel_MSG_WARN              = pb.MessageLevel_MSG_WARN
	MessageLevel_MSG_INFO              = pb.MessageLevel_MSG_INFO
	MessageLevel_MSG_DBG               = pb.MessageLevel_MSG_DBG
	MessageLevel_MSG_ALL               = pb.MessageLevel_MSG_ALL
)

// =============================================================================
// STATUS MESSAGE TYPES
// =============================================================================

type (
	// LinuxCNC status types
	Position         = pb.Position
	TaskStatus       = pb.TaskStatus
	TrajectoryStatus = pb.TrajectoryStatus
	PositionStatus   = pb.PositionStatus
	JointStatus      = pb.JointStatus
	AxisStatus       = pb.AxisStatus
	SpindleStatus    = pb.SpindleStatus
	ToolEntry        = pb.ToolEntry
	ToolStatus       = pb.ToolStatus
	IOStatus         = pb.IOStatus
	GCodeStatus      = pb.GCodeStatus
	LimitStatus      = pb.LimitStatus
	ErrorMessage     = pb.ErrorMessage
	LinuxCNCStatus   = pb.LinuxCNCStatus

	// HAL status types
	HalValue         = pb.HalValue
	HalPinInfo       = pb.HalPinInfo
	HalSignalInfo    = pb.HalSignalInfo
	HalParamInfo     = pb.HalParamInfo
	HalComponentInfo = pb.HalComponentInfo
	HalSystemStatus  = pb.HalSystemStatus
	ValueChange      = pb.ValueChange
	ValueChangeBatch = pb.ValueChangeBatch
)

// HalValue oneof wrappers
type (
	HalValue_BitValue   = pb.HalValue_BitValue
	HalValue_FloatValue = pb.HalValue_FloatValue
	HalValue_S32Value   = pb.HalValue_S32Value
	HalValue_U32Value   = pb.HalValue_U32Value
	HalValue_S64Value   = pb.HalValue_S64Value
	HalValue_U64Value   = pb.HalValue_U64Value
)

// =============================================================================
// COMMAND TYPES
// =============================================================================

type (
	// LinuxCNC command types
	LinuxCNCCommand         = pb.LinuxCNCCommand
	CommandResponse         = pb.CommandResponse
	StateCommand            = pb.StateCommand
	ModeCommand             = pb.ModeCommand
	MdiCommand              = pb.MdiCommand
	JogCommand              = pb.JogCommand
	HomeCommand             = pb.HomeCommand
	UnhomeCommand           = pb.UnhomeCommand
	SpindleControlCommand              = pb.SpindleControlCommand
	SpindleOverrideCommand  = pb.SpindleOverrideCommand
	BrakeCommand            = pb.BrakeCommand
	FeedrateCommand         = pb.FeedrateCommand
	RapidrateCommand        = pb.RapidrateCommand
	MaxVelCommand           = pb.MaxVelCommand
	CoolantCommand          = pb.CoolantCommand
	ToolOffsetCommand       = pb.ToolOffsetCommand
	ProgramCommand          = pb.ProgramCommand
	AutoCommand             = pb.AutoCommand
	DigitalOutputCommand    = pb.DigitalOutputCommand
	AnalogOutputCommand     = pb.AnalogOutputCommand
	SetLimitCommand         = pb.SetLimitCommand
	OverrideConfigCommand   = pb.OverrideConfigCommand
	ProgramOptionsCommand   = pb.ProgramOptionsCommand
	TeleopCommand           = pb.TeleopCommand
	TrajModeCommand         = pb.TrajModeCommand
	OverrideLimitsCommand   = pb.OverrideLimitsCommand
	ResetInterpreterCommand = pb.ResetInterpreterCommand
	LoadToolTableCommand    = pb.LoadToolTableCommand
	TaskPlanSyncCommand     = pb.TaskPlanSyncCommand
	DebugCommand            = pb.DebugCommand
	OperatorMessageCommand  = pb.OperatorMessageCommand

	// HAL command types
	HalCommand              = pb.HalCommand
	HalCommandResponse      = pb.HalCommandResponse
	CreateComponentCommand  = pb.CreateComponentCommand
	CreatePinCommand        = pb.CreatePinCommand
	CreateParamCommand      = pb.CreateParamCommand
	SetPinValueCommand      = pb.SetPinValueCommand
	SetParamValueCommand    = pb.SetParamValueCommand
	CreateSignalCommand     = pb.CreateSignalCommand
	SetSignalValueCommand   = pb.SetSignalValueCommand
	ConnectCommand          = pb.ConnectCommand
	DisconnectCommand       = pb.DisconnectCommand
	ReadyCommand            = pb.ReadyCommand
	UnreadyCommand          = pb.UnreadyCommand
	ExitComponentCommand    = pb.ExitComponentCommand
	DeleteSignalCommand     = pb.DeleteSignalCommand
	SetMessageLevelCommand  = pb.SetMessageLevelCommand
	GetValueCommand         = pb.GetValueCommand
	QueryPinsCommand        = pb.QueryPinsCommand
	QuerySignalsCommand     = pb.QuerySignalsCommand
	QueryParamsCommand      = pb.QueryParamsCommand
	QueryComponentsCommand  = pb.QueryComponentsCommand
	ComponentExistsCommand  = pb.ComponentExistsCommand
	ComponentReadyCommand   = pb.ComponentReadyCommand
	PinHasWriterCommand     = pb.PinHasWriterCommand
)

// =============================================================================
// LINUXCNC COMMAND ONEOF WRAPPERS
// =============================================================================

type (
	LinuxCNCCommand_State            = pb.LinuxCNCCommand_State
	LinuxCNCCommand_Mode             = pb.LinuxCNCCommand_Mode
	LinuxCNCCommand_Mdi              = pb.LinuxCNCCommand_Mdi
	LinuxCNCCommand_Jog              = pb.LinuxCNCCommand_Jog
	LinuxCNCCommand_Home             = pb.LinuxCNCCommand_Home
	LinuxCNCCommand_Unhome           = pb.LinuxCNCCommand_Unhome
	LinuxCNCCommand_Spindle          = pb.LinuxCNCCommand_Spindle
	LinuxCNCCommand_SpindleOverride  = pb.LinuxCNCCommand_SpindleOverride
	LinuxCNCCommand_Brake            = pb.LinuxCNCCommand_Brake
	LinuxCNCCommand_Feedrate         = pb.LinuxCNCCommand_Feedrate
	LinuxCNCCommand_Rapidrate        = pb.LinuxCNCCommand_Rapidrate
	LinuxCNCCommand_Maxvel           = pb.LinuxCNCCommand_Maxvel
	LinuxCNCCommand_Coolant          = pb.LinuxCNCCommand_Coolant
	LinuxCNCCommand_ToolOffset       = pb.LinuxCNCCommand_ToolOffset
	LinuxCNCCommand_Program          = pb.LinuxCNCCommand_Program
	LinuxCNCCommand_DigitalOutput    = pb.LinuxCNCCommand_DigitalOutput
	LinuxCNCCommand_AnalogOutput     = pb.LinuxCNCCommand_AnalogOutput
	LinuxCNCCommand_SetLimit         = pb.LinuxCNCCommand_SetLimit
	LinuxCNCCommand_OverrideConfig   = pb.LinuxCNCCommand_OverrideConfig
	LinuxCNCCommand_ProgramOptions   = pb.LinuxCNCCommand_ProgramOptions
	LinuxCNCCommand_Teleop           = pb.LinuxCNCCommand_Teleop
	LinuxCNCCommand_TrajMode         = pb.LinuxCNCCommand_TrajMode
	LinuxCNCCommand_OverrideLimits   = pb.LinuxCNCCommand_OverrideLimits
	LinuxCNCCommand_ResetInterpreter = pb.LinuxCNCCommand_ResetInterpreter
	LinuxCNCCommand_LoadToolTable    = pb.LinuxCNCCommand_LoadToolTable
	LinuxCNCCommand_TaskPlanSync     = pb.LinuxCNCCommand_TaskPlanSync
	LinuxCNCCommand_Debug            = pb.LinuxCNCCommand_Debug
	LinuxCNCCommand_OperatorMessage  = pb.LinuxCNCCommand_OperatorMessage
)

// ProgramCommand oneof wrappers
type (
	ProgramCommand_Open        = pb.ProgramCommand_Open
	ProgramCommand_RunFromLine = pb.ProgramCommand_RunFromLine
	ProgramCommand_Pause       = pb.ProgramCommand_Pause
	ProgramCommand_Resume      = pb.ProgramCommand_Resume
	ProgramCommand_Step        = pb.ProgramCommand_Step
	ProgramCommand_Abort       = pb.ProgramCommand_Abort
)

// =============================================================================
// HAL COMMAND ONEOF WRAPPERS
// =============================================================================

type (
	HalCommand_CreateComponent = pb.HalCommand_CreateComponent
	HalCommand_Ready           = pb.HalCommand_Ready
	HalCommand_Unready         = pb.HalCommand_Unready
	HalCommand_ExitComponent   = pb.HalCommand_ExitComponent
	HalCommand_CreatePin       = pb.HalCommand_CreatePin
	HalCommand_SetPin          = pb.HalCommand_SetPin
	HalCommand_CreateParam     = pb.HalCommand_CreateParam
	HalCommand_SetParam        = pb.HalCommand_SetParam
	HalCommand_CreateSignal    = pb.HalCommand_CreateSignal
	HalCommand_SetSignal       = pb.HalCommand_SetSignal
	HalCommand_DeleteSignal    = pb.HalCommand_DeleteSignal
	HalCommand_Connect         = pb.HalCommand_Connect
	HalCommand_Disconnect      = pb.HalCommand_Disconnect
	HalCommand_GetValue        = pb.HalCommand_GetValue
	HalCommand_QueryPins       = pb.HalCommand_QueryPins
	HalCommand_QuerySignals    = pb.HalCommand_QuerySignals
	HalCommand_QueryParams     = pb.HalCommand_QueryParams
	HalCommand_QueryComponents = pb.HalCommand_QueryComponents
	HalCommand_ComponentExists = pb.HalCommand_ComponentExists
	HalCommand_ComponentReady  = pb.HalCommand_ComponentReady
	HalCommand_PinHasWriter    = pb.HalCommand_PinHasWriter
	HalCommand_SetMessageLevel = pb.HalCommand_SetMessageLevel
)

// =============================================================================
// REQUEST/RESPONSE TYPES
// =============================================================================

type (
	// LinuxCNC request/response types
	GetStatusRequest    = pb.GetStatusRequest
	WaitCompleteRequest = pb.WaitCompleteRequest
	StreamStatusRequest = pb.StreamStatusRequest
	StreamErrorsRequest = pb.StreamErrorsRequest

	// File management types
	UploadFileRequest  = pb.UploadFileRequest
	UploadFileResponse = pb.UploadFileResponse
	ListFilesRequest   = pb.ListFilesRequest
	ListFilesResponse  = pb.ListFilesResponse
	FileInfo           = pb.FileInfo
	DeleteFileRequest  = pb.DeleteFileRequest
	DeleteFileResponse = pb.DeleteFileResponse

	// HAL request/response types
	GetSystemStatusRequest  = pb.GetSystemStatusRequest
	HalStreamStatusRequest  = pb.HalStreamStatusRequest
	WatchRequest            = pb.WatchRequest
	GetValueResponse        = pb.GetValueResponse
	BoolResponse            = pb.BoolResponse
	QueryPinsResponse       = pb.QueryPinsResponse
	QuerySignalsResponse    = pb.QuerySignalsResponse
	QueryParamsResponse     = pb.QueryParamsResponse
	QueryComponentsResponse = pb.QueryComponentsResponse
)

// =============================================================================
// SERVICE INTERFACES AND CLIENTS
// =============================================================================

type (
	// LinuxCNC service
	LinuxCNCServiceClient = pb.LinuxCNCServiceClient
	LinuxCNCServiceServer = pb.LinuxCNCServiceServer

	// LinuxCNC streaming types
	LinuxCNCService_StreamStatusClient = pb.LinuxCNCService_StreamStatusClient
	LinuxCNCService_StreamStatusServer = pb.LinuxCNCService_StreamStatusServer
	LinuxCNCService_StreamErrorsClient = pb.LinuxCNCService_StreamErrorsClient
	LinuxCNCService_StreamErrorsServer = pb.LinuxCNCService_StreamErrorsServer

	// HAL service
	HalServiceClient = pb.HalServiceClient
	HalServiceServer = pb.HalServiceServer

	// HAL streaming types
	HalService_StreamStatusClient = pb.HalService_StreamStatusClient
	HalService_StreamStatusServer = pb.HalService_StreamStatusServer
	HalService_WatchValuesClient  = pb.HalService_WatchValuesClient
	HalService_WatchValuesServer  = pb.HalService_WatchValuesServer

	// Unimplemented server types (for embedding)
	UnimplementedLinuxCNCServiceServer = pb.UnimplementedLinuxCNCServiceServer
	UnimplementedHalServiceServer      = pb.UnimplementedHalServiceServer
)

// =============================================================================
// FACTORY FUNCTIONS
// =============================================================================

// NewLinuxCNCServiceClient creates a new LinuxCNC service client.
var NewLinuxCNCServiceClient = pb.NewLinuxCNCServiceClient

// NewHalServiceClient creates a new HAL service client.
var NewHalServiceClient = pb.NewHalServiceClient

// RegisterLinuxCNCServiceServer registers a LinuxCNC service server.
var RegisterLinuxCNCServiceServer = pb.RegisterLinuxCNCServiceServer

// RegisterHalServiceServer registers a HAL service server.
var RegisterHalServiceServer = pb.RegisterHalServiceServer

// =============================================================================
// HIGH-LEVEL CLIENT
// =============================================================================

// Client provides a high-level interface to LinuxCNC gRPC services.
type Client struct {
	conn     *grpc.ClientConn
	LinuxCNC LinuxCNCServiceClient
	HAL      HalServiceClient
}

// NewClient creates a new LinuxCNC client connected to the specified address.
func NewClient(address string, opts ...grpc.DialOption) (*Client, error) {
	if len(opts) == 0 {
		opts = []grpc.DialOption{grpc.WithTransportCredentials(insecure.NewCredentials())}
	}

	conn, err := grpc.NewClient(address, opts...)
	if err != nil {
		return nil, err
	}

	return &Client{
		conn:     conn,
		LinuxCNC: pb.NewLinuxCNCServiceClient(conn),
		HAL:      pb.NewHalServiceClient(conn),
	}, nil
}

// Close closes the underlying gRPC connection.
func (c *Client) Close() error {
	return c.conn.Close()
}

// GetStatus retrieves the current LinuxCNC status.
func (c *Client) GetStatus(ctx context.Context) (*LinuxCNCStatus, error) {
	return c.LinuxCNC.GetStatus(ctx, &GetStatusRequest{})
}

// SendCommand sends a command to LinuxCNC.
func (c *Client) SendCommand(ctx context.Context, cmd *LinuxCNCCommand) (*CommandResponse, error) {
	return c.LinuxCNC.SendCommand(ctx, cmd)
}

// GetHALStatus retrieves the current HAL system status.
func (c *Client) GetHALStatus(ctx context.Context) (*HalSystemStatus, error) {
	return c.HAL.GetSystemStatus(ctx, &GetSystemStatusRequest{})
}

// UploadFile uploads a G-code file to the nc_files directory.
func (c *Client) UploadFile(ctx context.Context, filename, content string, failIfExists bool) (*UploadFileResponse, error) {
	return c.LinuxCNC.UploadFile(ctx, &UploadFileRequest{
		Filename:     filename,
		Content:      content,
		FailIfExists: failIfExists,
	})
}

// ListFiles lists files in the nc_files directory.
func (c *Client) ListFiles(ctx context.Context, subdirectory string) (*ListFilesResponse, error) {
	return c.LinuxCNC.ListFiles(ctx, &ListFilesRequest{Subdirectory: subdirectory})
}

// DeleteFile deletes a file from the nc_files directory.
func (c *Client) DeleteFile(ctx context.Context, filename string) (*DeleteFileResponse, error) {
	return c.LinuxCNC.DeleteFile(ctx, &DeleteFileRequest{Filename: filename})
}
