"""
Spout Specification

This module is called spout to mirror organization of storm package.
"""

from pystorm_a8c.dsl.component import ShellComponentSpec


class ShellSpoutSpec(ShellComponentSpec):
    def __init__(
        self,
        component_cls,
        name=None,
        command="pystorm_a8c_run",
        script=None,
        par=1,
        config=None,
        outputs=None,
    ):
        super().__init__(
            component_cls,
            name=name,
            par=par,
            config=config,
            outputs=outputs,
            command=command,
            script=script,
        )
