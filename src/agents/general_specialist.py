"""A specialist the owner defines, not one a developer predicted.

EmailAgent is a specialist whose domain needed code: a mailbox, analyzers, a
read-only Gmail boundary. Most specialists a person will ask for - "a 3D
specialist", "a coding specialist", something nobody here has thought of -
need none: what makes them specialists is who they are (an identity with its
own name, voice and personality), how they think (their own Brain, reasoning
about their own area of responsibility), what they remember (their own
memory), and what they are authorised to do (the registered capabilities the
owner grants them in the Control Center).

That is exactly what this class is, and nothing else. It holds no domain
code, so defining a new specialist is data - an identity and a set of grants -
never a new class, and never a second Manager: it cannot list, invite or
route to other agents, because nothing it holds reaches them.

Its work, in order:

    conversation floor        greeting, "who are you", its own voice, its own
                              recordings, the person's context (read-only)
    authorised capabilities   whatever the owner granted, run as itself
    its own Brain             anything else, answered honestly - with no
                              model configured it says so rather than guess

Capabilities are granted, never owned: a 3D specialist being granted
``create_3d_model`` takes nothing from the Manager, who keeps every
capability, nor from any other agent granted the same one.
"""

from __future__ import annotations

from src.agents.brain import SpecialistBrain
from src.agents.cognitive_agent import CognitiveAgent
from src.agents.tools.toolset import ToolSet
from src.core.contracts import AgentSpec, Task, TaskResult
from src.services.identity.personality import AgentPersonality, default_personality
from src.services.identity.specialist import SpecialistIdentity
from src.services.identity.voice import VoiceActivation, VoiceProfile, default_activation, default_voice
from src.services.memory.day import Calendar
from src.services.memory.memory import Memory
from src.services.memory.policy import MemoryPolicy
from src.services.memory.query import distinctive_terms
from src.utils.logging import get_logger, task_logger

logger = get_logger(__name__)

# What a specialist nobody has granted anything yet declares, so its spec is
# valid and no natural word in a request can route unrelated work to it.
UNGRANTED_CAPABILITY = "own_brain"


class GeneralSpecialist(CognitiveAgent):
    """A specialist made of an identity, a Brain, a memory and grants."""

    def __init__(
        self,
        identity: SpecialistIdentity,
        *,
        brain: SpecialistBrain | None = None,
        memory: Memory | None = None,
        memory_policy: MemoryPolicy | None = None,
        tools: ToolSet | None = None,
        calendar: Calendar | None = None,
        celebration=None,
    ) -> None:
        if not isinstance(identity, SpecialistIdentity):
            raise TypeError(f"Expected a SpecialistIdentity, got {type(identity).__name__}")
        super().__init__(
            brain=brain, memory=memory, memory_policy=memory_policy,
            tools=tools, calendar=calendar, celebration=celebration,
        )
        self._identity = identity

    # ---- identity ---------------------------------------------------------

    @property
    def identity(self) -> SpecialistIdentity:
        return self._identity

    @property
    def name(self) -> str:
        """Its name straight from its identity - the spec below asks who is
        granted what *by name*, so the name must not come from the spec."""
        return self._identity.name

    @property
    def spec(self) -> AgentSpec:
        """Its name, and what it declares: the capabilities it is granted
        right now (read live, so a grant made in the Control Center is what
        the next "what can you do" sees) and the distinctive words of its
        own area of responsibility - its domain vocabulary, the same signal
        the router and Alfred's collaboration already read for every
        specialist. Declaring words routes nothing the Manager's own
        capabilities claim: those are always his first."""
        declared = set(self.authorised_capabilities())
        declared |= set(distinctive_terms(self._identity.area_of_responsibility).split())
        return AgentSpec(
            name=self._identity.name,
            description=(self._identity.description or self._identity.role
                         or self._identity.area_of_responsibility or "A specialist."),
            capabilities=frozenset(declared) or frozenset({UNGRANTED_CAPABILITY}),
        )

    @property
    def brain_domain(self) -> str:
        """What its own Brain reasons about - its area of responsibility.
        Read by the composition root when the reasoning provider is switched
        live, so its Brain is rebuilt from the same area, never another's."""
        return self._identity.area_of_responsibility or "general work for the person this system serves"

    @property
    def voice(self) -> VoiceProfile:
        declared = self._identity.voice
        return declared if declared is not None else default_voice(self._identity.name)

    @property
    def activation(self) -> VoiceActivation:
        declared = self._identity.activation
        return declared if declared is not None else default_activation(self._identity.name)

    @property
    def personality(self) -> AgentPersonality:
        declared = self._identity.personality
        return declared if declared is not None else default_personality(self._identity.name)

    @property
    def interface(self):
        """Conversation, activity and a Workspace - the surfaces every agent's
        work has. A description of shape, never a grant."""
        from src.control_center.interfaces import InterfaceDescriptor, InterfaceFeature

        features = frozenset({InterfaceFeature.CHAT, InterfaceFeature.ACTIVITY, InterfaceFeature.WORKSPACE})
        return InterfaceDescriptor(
            agent=self._identity.name,
            display_name=self._identity.name,
            features=features,
            implemented=features,
            summary=(self._identity.area_of_responsibility or self._identity.role or "a specialist")[:120],
        )

    # ---- work -------------------------------------------------------------

    def execute(self, task: Task) -> TaskResult:
        log = task_logger(logger, task.id)
        spoken = self.conversational_reply(task)
        if spoken is not None:
            log.debug("answered by the conversation floor or an authorised capability")
            return spoken
        return self.general_conversation(task)
