"use client";

import { Tabs } from "@heroui/react";

import type { SectionId } from "@/shared/grid/grid-config";

/** Minimal shape the strip needs — the «Ремонт» form tabs have no section id. */
export type SectionTab = {
    id: string;
    label: string;
    /** Starts a new group: the strip draws a separator before this tab. */
    separatorBefore?: boolean;
};

export type WorkspaceTab = SectionTab & {
    section: SectionId;
    reportMonth: string | null;
};

export function SectionTabs<T extends SectionTab>({
    tabs,
    value,
    onChange,
}: {
    tabs: readonly T[];
    value: string;
    onChange: (tab: T) => void;
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
                    {tabs.map((tab) => (
                        <Tabs.Tab key={tab.id} id={tab.id}>
                            {tab.separatorBefore ? <Tabs.Separator /> : null}
                            {tab.label}
                            <Tabs.Indicator />
                        </Tabs.Tab>
                    ))}
                </Tabs.List>
            </Tabs.ListContainer>
        </Tabs>
    );
}
