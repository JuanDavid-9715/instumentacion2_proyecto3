"""Core of the vision system: capture, detection, state and actuator.

None of these modules import the web framework. ``detector`` and ``state`` do
not touch hardware at all, so both run on the development PC.
"""
