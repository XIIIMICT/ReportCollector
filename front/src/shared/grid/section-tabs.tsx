"use client";

import { Tabs } from "@heroui/react";

import { MAIN_SECTIONS } from "@/shared/grid/grid-config";
import type { SectionId } from "@/shared/grid/grid-config";

export type WorkspaceTab = {
    id: string;
    section: SectionId;
    label: string;
    reportMonth: string | null;
};

export function SectionTabs({
    tabs,
    value,
    onChange,
}: {
    tabs: WorkspaceTab[];
    value: string;
    onChange: (tab: WorkspaceTab) => void;
}) {
    return (
        <Tabs
            className="section-tabs"
            selectedKey={value}
            variant="primary"
            onSelectionChange={(key) => {
                const tab = tabs.find((candidate) => candidate.id === String(key));
                if (tab) onChange(tab);
            }}
        >
            <Tabs.ListContainer>
                <Tabs.List aria-label="Таблицы и справочники">
                    {tabs.map((tab, index) => {
                        const previous = tabs[index - 1];
                        const isFirstReference =
                            index > 0 &&
                            !MAIN_SECTIONS.has(tab.section) &&
                            MAIN_SECTIONS.has(previous.section);
                        return (
                            <Tabs.Tab key={tab.id} id={tab.id}>
                                {isFirstReference ? <Tabs.Separator /> : null}
                                {tab.label}
                                <Tabs.Indicator />
                            </Tabs.Tab>
                        );
                    })}
                </Tabs.List>
            </Tabs.ListContainer>
        </Tabs>
    );
}
