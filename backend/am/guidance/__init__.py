"""'Is this serious?' guidance, escalation events and the body map (feature F7).

The alert service imports `am.guidance.escalation` on the measurement ingest path, so this
package initialiser stays empty: importing it must not pull in the body map module.
"""
