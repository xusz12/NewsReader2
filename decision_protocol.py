import math


PROVIDERS = ("jev", "openai-decisions")
OPENAI_MODELS = ("gpt-6-luna",)


def alternate(provider):
    first, second = PROVIDERS
    if provider == first:
        return second
    if provider == second:
        return first
    raise ValueError("unsupported_decision_provider")


def request_payload(provider, model, state, questions):
    if provider == "jev":
        return {"model": model, "state": state, "questions": questions}
    if provider != "openai-decisions":
        raise ValueError("unsupported_decision_provider")
    if model not in OPENAI_MODELS:
        raise ValueError("unsupported_decision_model")
    converted = []
    for name, question in questions.items():
        instructions = question["instructions"]
        if question["type"] == "noul":
            for value, description in question.get("criteria", {}).items():
                instructions += f"\n{value}: {description}"
            converted.append({"name": name, "type": "predicate", "instructions": instructions})
        elif question["type"] == "choice":
            converted.append({"name": name, "type": "choice", "instructions": instructions,
                              "choices": [{"value": value, "description": description}
                                          for value, description in question["criteria"].items()]})
        else:
            raise ValueError("unsupported_decision_question")
    evidence = state if isinstance(state, str) else [
        {"role": "user", "content": [{"type": "input_json", "data": state}]}]
    return {"model": model, "input": evidence, "questions": converted}


def probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def normalize_response(provider, payload, questions):
    try:
        if not isinstance(payload, dict) or payload.get("error"):
            raise ValueError("invalid_response")
        model = payload["model"]
        if not isinstance(model, str) or not model.strip():
            raise ValueError("invalid_response")
        answers = payload["answers"]
        if provider == "openai-decisions":
            if not isinstance(answers, list) or len(answers) != len(questions):
                raise ValueError("invalid_response")
            converted = {}
            for answer in answers:
                name = answer["name"]
                if not isinstance(name, str) or name not in questions or name in converted:
                    raise ValueError("invalid_response")
                kind = answer["type"]
                if kind == "refusal":
                    raise ValueError("decision_refused")
                if kind == "predicate":
                    converted[name] = {"type": "noul", "noul": answer["probability"]}
                elif kind == "choice":
                    probabilities = {}
                    if not isinstance(answer["probabilities"], list):
                        raise ValueError("invalid_response")
                    for entry in answer["probabilities"]:
                        value = entry["value"]
                        if not isinstance(value, str) or value in probabilities:
                            raise ValueError("invalid_response")
                        probabilities[value] = entry["probability"]
                    converted[name] = {"type": "choice", "choice": answer["choice"],
                                       "confidence": answer["confidence"], "probabilities": probabilities}
                else:
                    raise ValueError("invalid_response")
            answers = converted
        elif provider != "jev":
            raise ValueError("invalid_response")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("invalid_response")
        for name, question in questions.items():
            answer = answers[name]
            if answer["type"] != question["type"]:
                raise ValueError("invalid_response")
            if question["type"] == "noul":
                valid = probability(answer["noul"])
            else:
                probabilities, choice = answer["probabilities"], answer["choice"]
                valid = (isinstance(probabilities, dict) and set(probabilities) == set(question["criteria"])
                         and isinstance(choice, str) and choice in probabilities
                         and all(probability(p) for p in probabilities.values())
                         and abs(sum(probabilities.values()) - 1) < .02
                         and probability(answer["confidence"])
                         and probabilities[choice] >= max(probabilities.values()))
            if not valid:
                raise ValueError("invalid_response")
        return {"model": model, "answers": answers, "usage": payload.get("usage", {})}
    except (KeyError, TypeError, AttributeError):
        raise ValueError("invalid_response") from None
