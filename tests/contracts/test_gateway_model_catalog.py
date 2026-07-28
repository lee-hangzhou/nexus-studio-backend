from app.contracts.gateway import GatewayModelsResponse
from app.server.generation import assembly


def test_gateway_models_response_requires_parameters() -> None:
    payload = {
        "object": "list",
        "data": [
            {
                "id": "seedream5.0",
                "object": "model",
                "task_type": 2,
                "supports_vision": False,
                "supports_video_input": False,
                "parameters": {
                    "resolutions": ["2k", "3k"],
                    "ratios": ["1:1", "4:3"],
                    "ratios_by_resolution": {},
                    "counts": [1, 2, 3, 4, 5, 6],
                    "durations": [],
                    "reference_modes": [],
                    "material_limits": {"images": 10},
                    "input_schema": None,
                },
            }
        ],
    }
    parsed = GatewayModelsResponse.model_validate(payload)
    item = parsed.data[0]
    assert item.parameters.resolutions == ["2k", "3k"]
    assert "1k" not in item.parameters.resolutions
    caps = assembly.capabilities_from_gateway_model(item)
    assert caps is not None
    assert caps.resolutions == ("2k", "3k")
    assert caps.material_limits.images == 10
