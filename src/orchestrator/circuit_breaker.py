"""
Circuit Breaker
===============
Prevents cascading failures by stopping calls to a failing service
after repeated errors. Self-heals after a timeout period.
"""

import time
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"       # Healthy — calls pass through
    OPEN = "open"           # Broken — calls are rejected
    HALF_OPEN = "half_open" # Testing — allow one call to test recovery


class CircuitBreaker:
    """
    Circuit breaker for sub-agent fault tolerance.
    
    States:
    - CLOSED: Agent is healthy, all calls pass through
    - OPEN: Agent has failed too many times, calls are rejected
    - HALF_OPEN: After reset_timeout, allow one test call
    
    Usage:
        breaker = CircuitBreaker("story", max_failures=3, reset_timeout=60)
        
        if breaker.is_open():
            return fallback_response()
        
        try:
            result = await agent.call()
            breaker.record_success()
            return result
        except Exception:
            breaker.record_failure()
            return fallback_response()
    """

    def __init__(self, name: str, max_failures: int = 3, reset_timeout: float = 60.0):
        self.name = name
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0
        self.success_count = 0

    def is_open(self) -> bool:
        """Check if circuit is open (agent is considered down)."""
        if self.state == CircuitState.CLOSED:
            return False

        if self.state == CircuitState.OPEN:
            # Check if enough time has passed to try again
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    f"Circuit breaker {self.name}: OPEN → HALF_OPEN (testing)",
                    extra={"event": "circuit_half_open", "agent": self.name},
                )
                return False  # Allow one test call
            return True  # Still broken

        # HALF_OPEN — allow the test call
        return False

    def record_failure(self):
        """Record a failure. May trip the circuit breaker."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Test call failed — back to OPEN
            self.state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker {self.name}: HALF_OPEN → OPEN (test failed)",
                extra={"event": "circuit_open", "agent": self.name},
            )
        elif self.failure_count >= self.max_failures:
            self.state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker {self.name}: CLOSED → OPEN ({self.failure_count} failures)",
                extra={"event": "circuit_open", "agent": self.name},
            )

    def record_success(self):
        """Record a success. Resets the circuit breaker."""
        if self.state == CircuitState.HALF_OPEN:
            logger.info(
                f"Circuit breaker {self.name}: HALF_OPEN → CLOSED (recovered)",
                extra={"event": "circuit_closed", "agent": self.name},
            )

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count += 1

    def get_status(self) -> dict:
        """Get circuit breaker status for observability."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure": self.last_failure_time,
        }
