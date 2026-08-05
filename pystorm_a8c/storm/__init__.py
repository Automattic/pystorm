"""Version facade over the Storm Thrift IDL.

This is the ONLY module in the package permitted to import a versioned IDL
module. Everything else imports from here, so moving to Storm 2.x means
adding a new IDL module and changing the import below.
"""

from pystorm_a8c.storm.thrift_idl_1x import storm_thrift

STORM_IDL_VERSION = "1.x"

StormTopology = storm_thrift.StormTopology
ThriftBolt = storm_thrift.Bolt
SpoutSpec = storm_thrift.SpoutSpec
ComponentCommon = storm_thrift.ComponentCommon
ComponentObject = storm_thrift.ComponentObject
ShellComponent = storm_thrift.ShellComponent
GlobalStreamId = storm_thrift.GlobalStreamId
ThriftGrouping = storm_thrift.Grouping
StreamInfo = storm_thrift.StreamInfo
NullStruct = storm_thrift.NullStruct
# Grouping.custom_object builds a JavaObject: a custom grouping is a Java
# class even when every component is Python.
JavaObject = storm_thrift.JavaObject
JavaObjectArg = storm_thrift.JavaObjectArg
SubmitOptions = storm_thrift.SubmitOptions
TopologyInitialStatus = storm_thrift.TopologyInitialStatus
KillOptions = storm_thrift.KillOptions
Nimbus = storm_thrift.Nimbus
AuthorizationException = storm_thrift.AuthorizationException
NotAliveException = storm_thrift.NotAliveException
AlreadyAliveException = storm_thrift.AlreadyAliveException
InvalidTopologyException = storm_thrift.InvalidTopologyException

# thriftpy2 structs are unhashable by default, and the DSL uses GlobalStreamId
# as a dict key when building ComponentCommon.inputs.
GlobalStreamId.__hash__ = lambda self: hash(self.componentId) ^ hash(self.streamId)

__all__ = [
    "STORM_IDL_VERSION",
    "StormTopology",
    "ThriftBolt",
    "SpoutSpec",
    "ComponentCommon",
    "ComponentObject",
    "ShellComponent",
    "GlobalStreamId",
    "ThriftGrouping",
    "StreamInfo",
    "NullStruct",
    "JavaObject",
    "JavaObjectArg",
    "SubmitOptions",
    "TopologyInitialStatus",
    "KillOptions",
    "Nimbus",
    "AuthorizationException",
    "NotAliveException",
    "AlreadyAliveException",
    "InvalidTopologyException",
]
