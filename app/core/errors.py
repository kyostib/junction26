class ProviderConfigurationError(RuntimeError):
    """A required external provider is not configured."""


class ProviderUnavailableError(RuntimeError):
    """A configured external provider did not produce a usable response."""


class SocialDataUnavailableError(RuntimeError):
    """Public posts could not be collected with verifiable post identifiers."""
