"""What the ecosystem does on its own, within what the owner declared.

    conditions.py   declared conditions over observations -> events -> a
                    notification or a *proposed* action (GM-02)
    device_tasks.py long-running work handed to a device, supervised (GM-11)
    tick.py         the one scheduled step that runs whatever is due

Nothing here acts beyond what was declared, and nothing consequential ever
runs without the person: a condition may *propose* an action, which then
waits at the same confirmation gate every consequential action already
passes through.
"""
