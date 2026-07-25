export function engagementEntityHref(
  slug: string,
  entity: { type: string; value: string },
): string {
  const params = new URLSearchParams({
    slug,
    view: "entities",
    type: entity.type,
    value: entity.value,
  });
  return `/e?${params.toString()}`;
}
