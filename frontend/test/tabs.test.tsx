import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";

describe("Tabs accessibility", () => {
  it("associates tabs with panels and supports arrow-key navigation", async () => {
    const user = userEvent.setup();
    render(
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="entities">Entities</TabsTrigger>
        </TabsList>
        <TabsContent value="overview">Overview panel</TabsContent>
        <TabsContent value="entities">Entities panel</TabsContent>
      </Tabs>,
    );

    const overview = screen.getByRole("tab", { name: "Overview" });
    const entities = screen.getByRole("tab", { name: "Entities" });
    expect(overview).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      overview.id,
    );

    overview.focus();
    await user.keyboard("{ArrowRight}");

    expect(entities).toHaveFocus();
    expect(entities).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Entities panel");
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      entities.id,
    );
  });
});
