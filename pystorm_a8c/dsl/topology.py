"""
Topology base class
"""

from pystorm_a8c.component import Component
from pystorm_a8c.dsl.bolt import ShellBoltSpec
from pystorm_a8c.dsl.component import ComponentSpec
from pystorm_a8c.dsl.spout import ShellSpoutSpec
from pystorm_a8c.storm import SpoutSpec, StormTopology, ThriftBolt


class TopologyType(type):
    """Class to define a Storm topology in a Python DSL."""

    def __new__(mcs, classname, bases, class_dict):
        bolt_specs = {}
        spout_specs = {}
        # Copy ComponentSpec items out of class_dict
        specs = TopologyType.class_dict_to_specs(class_dict)
        # Perform checks
        for spec in specs.values():
            if isinstance(spec, ShellBoltSpec):
                TopologyType.add_bolt_spec(spec, bolt_specs)
            elif isinstance(spec, ShellSpoutSpec):
                TopologyType.add_spout_spec(spec, spout_specs)
            else:
                raise TypeError(
                    f"Specifications should either be bolts or spouts.  Given: {spec!r}"
                )
            TopologyType.clean_spec_inputs(spec, specs)
        if classname != "Topology" and not spout_specs:
            raise ValueError("A Topology requires at least one Spout")
        if "config" in class_dict:
            config_dict = class_dict["config"]
            if not isinstance(config_dict, dict):
                raise TypeError(
                    f"Topology config must be a dictionary. Given: {config_dict!r}"
                )
        else:
            class_dict["config"] = {}
        class_dict["thrift_bolts"] = bolt_specs
        class_dict["thrift_spouts"] = spout_specs
        class_dict["specs"] = list(specs.values())
        class_dict["thrift_topology"] = StormTopology(
            spouts=spout_specs, bolts=bolt_specs, state_spouts={}
        )
        return type.__new__(mcs, classname, bases, class_dict)

    @classmethod
    def class_dict_to_specs(mcs, class_dict):
        """Extract valid `ComponentSpec` entries from `Topology.__dict__`."""
        specs = {}
        # Set spec names first
        for name, spec in class_dict.items():
            if isinstance(spec, ComponentSpec):
                # Use the variable name as the specification name.
                if spec.name is None:
                    spec.name = name
                if spec.name in specs:
                    raise ValueError(f"Duplicate component name: {spec.name}")
                else:
                    specs[spec.name] = spec
            elif isinstance(spec, Component):
                raise TypeError(
                    "Topology classes should have ComponentSpec "
                    "attributes.  Did you forget to call the spec "
                    "class method for your component?  Given: {!r}".format(spec)
                )
        return specs

    @classmethod
    def add_bolt_spec(mcs, spec, bolt_specs):
        """Add valid Bolt specs to `bolt_specs`; raise exceptions for others."""
        if not spec.inputs:
            cls_name = spec.component_cls.__name__
            raise ValueError(
                '{} "{}" requires at least one input, because it '
                "is a Bolt.".format(cls_name, spec.name)
            )
        bolt_specs[spec.name] = ThriftBolt(
            bolt_object=spec.component_object, common=spec.common
        )

    @classmethod
    def add_spout_spec(mcs, spec, spout_specs):
        """Add valid Spout specs to `spout_specs`; raise exceptions for others."""
        if not spec.outputs:
            cls_name = spec.component_cls.__name__
            raise ValueError(
                '{} "{}" requires at least one output, because it '
                "is a Spout".format(cls_name, spec.name)
            )
        spout_specs[spec.name] = SpoutSpec(
            spout_object=spec.component_object, common=spec.common
        )

    @classmethod
    def clean_spec_inputs(mcs, spec, specs):
        """Convert `spec.inputs` to a dict mapping from stream IDs to groupings."""
        if spec.inputs is None:
            spec.inputs = {}
        for stream_id, grouping in list(spec.inputs.items()):
            if isinstance(stream_id.componentId, ComponentSpec):
                # Have to reinsert key after fix because hash changes
                del spec.inputs[stream_id]
                stream_id.componentId = stream_id.componentId.name
                spec.inputs[stream_id] = grouping
            # This should never happen, but it's worth checking for
            elif stream_id.componentId is None:
                raise TypeError("GlobalStreamId.componentId cannot be None.")
            # Check for invalid fields grouping
            stream_comp = specs[stream_id.componentId]
            valid_fields = set(stream_comp.outputs[stream_id.streamId].output_fields)
            if grouping.fields is not None:
                for field in grouping.fields:
                    if field not in valid_fields:
                        raise ValueError(
                            "Field {!r} specified in grouping is "
                            "not a valid output field for the {!r}"
                            " {!r} stream.".format(
                                field, stream_comp.name, stream_id.streamId
                            )
                        )

    def __repr__(cls):
        """:returns: A string representation of the topology"""
        # TODO: Come up with a better repr that makes it clear the class is not
        #       actually a StormTopology object
        return repr(getattr(cls, "thrift_topology", None))


class Topology(metaclass=TopologyType):
    """Class to define a Storm topology in a Python DSL."""
