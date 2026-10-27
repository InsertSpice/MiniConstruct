"""Technical Subject slots, independent of opaque IDs and narrative names."""


def subject_numbers(subjects) -> dict[str, int]:
    return {subject.id: subject.number for subject in subjects}


def subject_labels(subjects) -> dict[str, str]:
    return {subject_id: f"Subject {number}" for subject_id, number in subject_numbers(subjects).items()}
