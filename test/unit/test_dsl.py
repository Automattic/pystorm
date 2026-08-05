"""
Tests for Topology DSL
"""

import importlib
import logging
import unittest
from io import BytesIO

from pystorm_a8c.bolt import Bolt
from pystorm_a8c.component import Component
from pystorm_a8c.dsl import Grouping, Stream, Topology
from pystorm_a8c.dsl.bolt import ShellBoltSpec
from pystorm_a8c.dsl.spout import ShellSpoutSpec
from pystorm_a8c.spout import Spout

log = logging.getLogger(__name__)


class WordSpout(Spout):
    outputs = ["word"]


class WordCountBolt(Bolt):
    outputs = ["word", "count"]


class MultiStreamWordCountBolt(Bolt):
    outputs = [
        Stream(fields=["word", "count"]),
        Stream(fields=["all_word_count"], name="sum"),
    ]


class DatabaseDumperBolt(Bolt):
    outputs = []


class TopologyTests(unittest.TestCase):
    maxDiff = 1000

    def test_basic_spec(self):
        class WordCount(Topology):
            word_spout = WordSpout.spec(par=2)
            word_bolt = WordCountBolt.spec(
                inputs={word_spout: Grouping.fields("word")}, par=8
            )

        self.assertEqual(len(WordCount.specs), 2)
        self.assertEqual(
            list(WordCount.word_bolt.inputs.keys()), [WordCount.word_spout["default"]]
        )
        self.assertEqual(
            WordCount.thrift_spouts["word_spout"].common.parallelism_hint, 2
        )
        self.assertEqual(WordCount.thrift_bolts["word_bolt"].common.parallelism_hint, 8)
        self.assertEqual(
            WordCount.word_bolt.inputs[WordCount.word_spout["default"]],
            Grouping.fields("word"),
        )

    def test_spec_with_inputs_as_list(self):
        class WordCount(Topology):
            word_spout = WordSpout.spec(par=2)
            word_bolt = WordCountBolt.spec(inputs=[word_spout], par=8)

        self.assertEqual(len(WordCount.specs), 2)
        self.assertEqual(len(WordCount.thrift_spouts), 1)
        self.assertEqual(len(WordCount.thrift_bolts), 1)
        self.assertEqual(
            list(WordCount.word_bolt.inputs.keys()), [WordCount.word_spout["default"]]
        )
        self.assertEqual(
            WordCount.word_bolt.inputs[WordCount.word_spout["default"]],
            Grouping.SHUFFLE,
        )

    def test_multi_stream_bolt(self):
        class WordCount(Topology):
            word_spout = WordSpout.spec(par=2)
            word_bolt = MultiStreamWordCountBolt.spec(inputs=[word_spout], par=8)
            db_dumper_bolt = DatabaseDumperBolt.spec(
                par=4, inputs=[word_bolt["sum"], word_bolt["default"]]
            )

        self.assertEqual(len(WordCount.specs), 3)
        self.assertEqual(len(WordCount.thrift_spouts), 1)
        self.assertEqual(len(WordCount.thrift_bolts), 2)
        self.assertEqual(
            list(WordCount.word_bolt.inputs.keys()), [WordCount.word_spout["default"]]
        )
        self.assertEqual(
            WordCount.word_bolt.inputs[WordCount.word_spout["default"]],
            Grouping.SHUFFLE,
        )
        db_dumper_bolt_input_set = set(WordCount.db_dumper_bolt.inputs.keys())
        self.assertEqual(
            len(WordCount.db_dumper_bolt.inputs.keys()), len(db_dumper_bolt_input_set)
        )
        self.assertEqual(
            db_dumper_bolt_input_set,
            {WordCount.word_bolt["sum"], WordCount.word_bolt["default"]},
        )

    def test_long_chain_spec(self):
        class WordCount(Topology):
            word_spout = WordSpout.spec()
            word_bolt1 = WordCountBolt.spec(inputs=[word_spout])
            word_bolt2 = WordCountBolt.spec(inputs=[word_bolt1])
            word_bolt3 = WordCountBolt.spec(inputs=[word_bolt2])
            word_bolt4 = WordCountBolt.spec(inputs=[word_bolt3])
            word_bolt5 = WordCountBolt.spec(inputs=[word_bolt4])
            word_bolt6 = WordCountBolt.spec(inputs=[word_bolt5])
            word_bolt7 = WordCountBolt.spec(inputs=[word_bolt6])
            word_bolt8 = WordCountBolt.spec(inputs=[word_bolt7])
            word_bolt9 = WordCountBolt.spec(inputs=[word_bolt8])
            word_bolt10 = WordCountBolt.spec(inputs=[word_bolt9])
            word_bolt11 = WordCountBolt.spec(inputs=[word_bolt10])
            word_bolt12 = WordCountBolt.spec(inputs=[word_bolt11])

        self.assertEqual(len(WordCount.specs), 13)
        self.assertEqual(len(WordCount.thrift_spouts), 1)
        self.assertEqual(len(WordCount.thrift_bolts), 12)
        self.assertEqual(
            list(WordCount.word_bolt1.inputs.keys())[0], WordCount.word_spout["default"]
        )
        self.assertEqual(
            WordCount.word_bolt1.inputs[WordCount.word_spout["default"]],
            Grouping.SHUFFLE,
        )
        for i in range(2, 13):
            bolt = getattr(WordCount, f"word_bolt{i}")
            prev_bolt = getattr(WordCount, f"word_bolt{i - 1}")
            self.assertEqual(list(bolt.inputs.keys())[0], prev_bolt["default"])
            self.assertEqual(bolt.inputs[prev_bolt["default"]], Grouping.SHUFFLE)

    def test_many_spouts_spec(self):
        class WordCount(Topology):
            word_spout1 = WordSpout.spec()
            word_spout2 = WordSpout.spec()
            word_spout3 = WordSpout.spec()
            word_spout4 = WordSpout.spec()
            word_spout5 = WordSpout.spec()
            word_bolt = WordCountBolt.spec(
                inputs=[word_spout1, word_spout2, word_spout3, word_spout4, word_spout5]
            )

    def test_invalid_bolt_group_field(self):
        # Fields grouping must specify field output by spout
        with self.assertRaises(ValueError):

            class WordCount(Topology):
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(
                    inputs={word_spout: Grouping.fields("foo")}
                )

    def test_empty_bolt_group_field(self):
        # Fields groupings require field names to be specified
        with self.assertRaises(ValueError):

            class WordCount(Topology):
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(inputs={word_spout: Grouping.fields()})

    def test_duplicate_name(self):
        # Each component name must be unique
        with self.assertRaises(ValueError):

            class WordCount(Topology):
                word = WordSpout.spec()
                word_ = WordCountBolt.spec(name="word", inputs=[word])

    def test_no_input_bolt(self):
        # All bolts must have at least one input
        with self.assertRaises(ValueError):

            class WordCount(Topology):
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(inputs=[])

    def test_no_outputs_spout_empty(self):
        # All spouts must have output fields
        class PointlessSpout(Spout):
            outputs = []

        with self.assertRaises(ValueError):

            class WordCount(Topology):
                word_spout = PointlessSpout.spec()

    def test_no_outputs_spout(self):
        # All spouts must have output fields
        class PointlessSpout(Spout):
            outputs = []

        with self.assertRaises(ValueError):

            class WordCount(Topology):
                word_spout = PointlessSpout.spec()

    def test_base_component_rejection(self):
        # Topology components must inherit from Bolt/Spout, not Component directly
        class MyComponent(Component):
            outputs = []

        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = MyComponent.spec()

    def test_component_instead_of_spec(self):
        # Make sure we catch things early when people forget to call .spec
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = WordSpout(input_stream=BytesIO(), output_stream=BytesIO())

    def test_no_spout(self):
        # Every topology must have a spout
        with self.assertRaises(ValueError):

            class WordCount(Topology):
                pass

    def test_zero_par(self):
        # Component parallelism (number of processes) can't be 0
        with self.assertRaises(ValueError):

            class WordCount(Topology):
                word_spout = WordSpout.spec(par=0)

    def test_negative_par(self):
        # Component parallelism (number of processes) can't be negative
        with self.assertRaises(ValueError):

            class WordCount(Topology):
                word_spout = WordSpout.spec(par=-1)

    def test_float_par(self):
        # Component parallelism (number of processes) must be an int
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = WordSpout.spec(par=5.4)

    def test_dict_par(self):
        # Component parallelism (number of processes) can temporarily be a dict
        class WordCount(Topology):
            word_spout = WordSpout.spec(par={"prod": 5, "beta": 1})

        self.assertEqual(
            WordCount.thrift_spouts["word_spout"].common.parallelism_hint,
            {"prod": 5, "beta": 1},
        )

    def test_dict_par_bad_key_type(self):
        # Component parallelism dict must map for str to int
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = WordSpout.spec(par={1000: 5, "beta": 1})

    def test_dict_par_bad_value_type(self):
        # Component parallelism dict must map for str to int
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = WordSpout.spec(par={"prod": 5.5, "beta": 1})

    def test_invalid_bolt_input_dict_key(self):
        # Keys in input dict must be either GlobalStreamId or ComponentSpec
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(
                    inputs={"word_spout": Grouping.fields("word")}
                )

    def test_invalid_bolt_input_dict_val(self):
        # Values in input dict must be Grouping objects
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(inputs={word_spout: "word"})

    def test_invalid_bolt_input_str(self):
        # inputs must be either list, dict, or None; not str
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(inputs="word_spout")

    def test_invalid_config_str(self):
        # configs must be either dict, or None; not str
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(
                    inputs=[word_spout], config='{"foo": "bar"}'
                )

    def test_invalid_topology_config_str(self):
        # configs must be either dict, or None; not str
        with self.assertRaises(TypeError):

            class WordCount(Topology):
                config = '{"foo": "bar"}'
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(inputs=[word_spout])

    def test_invalid_outputs_entry_int(self):
        # Outputs must either be list of strings or list of streams, not ints
        class BadSpout(Spout):
            outputs = ["foo", 2]

        with self.assertRaises(TypeError):

            class WordCount(Topology):
                bad_spout = BadSpout.spec()
                word_bolt = WordCountBolt.spec(inputs=[bad_spout])

    def test_invalid_outputs_str(self):
        # Outputs must either be list of strings or list of streams
        class BadSpout(Spout):
            outputs = "foo"

        with self.assertRaises(TypeError):

            class WordCount(Topology):
                bad_spout = BadSpout.spec()
                word_bolt = WordCountBolt.spec(inputs=[bad_spout])

    def test_unknown_stream(self):
        # Should raise keyerror if stream name is not valid
        with self.assertRaises(KeyError):

            class WordCount(Topology):
                word_spout = WordSpout.spec()
                word_bolt = WordCountBolt.spec(inputs=[word_spout["word"]])


class ShellSpecTests(unittest.TestCase):
    """Validation coverage that used to run through ShellBolt/ShellSpout.

    Those two streamparse wrapper classes existed for non-Python components
    and are dropped, but the ShellComponentSpec argument checks behind them
    still guard every Python component -- so exercise the specs directly
    rather than losing the coverage with the classes.
    """

    def test_no_command_is_rejected(self):
        with self.assertRaises(ValueError):
            ShellBoltSpec(WordCountBolt, command=None, script="x", outputs=["word"])

    def test_no_script_is_rejected(self):
        with self.assertRaises(TypeError):
            ShellBoltSpec(WordCountBolt, command="perl", script=None, outputs=["word"])

    def test_spout_no_command_is_rejected(self):
        with self.assertRaises(ValueError):
            ShellSpoutSpec(WordSpout, command=None, script="x", outputs=["word"])

    def test_spout_no_script_is_rejected(self):
        with self.assertRaises(TypeError):
            ShellSpoutSpec(WordSpout, command="perl", script=None, outputs=["word"])

    def test_command_defaults_to_this_packages_runner(self):
        spec = ShellBoltSpec(WordCountBolt, script="x", outputs=["word"])
        self.assertEqual(
            spec.component_object.shell.execution_command, "pystorm-a8c-run"
        )


class ExecutionCommandTests(unittest.TestCase):
    def test_shell_spec_uses_the_run_console_script(self):
        class WordCount(Topology):
            word_spout = WordSpout.spec()
            word_bolt = WordCountBolt.spec(inputs=[word_spout])

        spout_shell = WordCount.thrift_spouts["word_spout"].spout_object.shell
        bolt_shell = WordCount.thrift_bolts["word_bolt"].bolt_object.shell
        self.assertEqual(spout_shell.execution_command, "pystorm-a8c-run")
        self.assertEqual(bolt_shell.execution_command, "pystorm-a8c-run")

    def test_script_is_a_dotted_path_the_runner_can_split(self):
        """pystorm-a8c-run does target.rsplit(".", 1) to get (module, class).

        A "-m module" form would rsplit into module "-m pkg" and class "mod",
        and every component would fail to import on the worker -- a break that
        only a real cluster would surface.
        """

        class WordCount(Topology):
            word_spout = WordSpout.spec()
            word_bolt = WordCountBolt.spec(inputs=[word_spout])

        script = WordCount.thrift_spouts["word_spout"].spout_object.shell.script
        self.assertEqual(script, f"{WordSpout.__module__}.WordSpout")
        mod_name, cls_name = script.rsplit(".", 1)
        self.assertEqual(cls_name, "WordSpout")
        self.assertNotIn(" ", mod_name)
        self.assertIs(getattr(importlib.import_module(mod_name), cls_name), WordSpout)
