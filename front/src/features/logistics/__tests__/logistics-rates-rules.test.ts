import { describe, expect, it } from "bun:test";

import {
    rateDirection,
    rateDirectionPreview,
    rateNetRub,
    rateNetUsd,
    rateUsd,
} from "../logistics-rates-rules";

describe("rateDirection", () => {
    it("classifies a shipment from Russia by the destination market", () => {
        expect(rateDirection("РФ", "РФ")).toBe("РФ");
        expect(rateDirection("РФ", "БЗ")).toBe("БЗ");
        expect(rateDirection("РФ", "ДЗ")).toBe("ДЗ");
    });

    it("falls back to «ДЗ» when the destination market is unknown", () => {
        expect(rateDirection("РФ", undefined)).toBe("ДЗ");
    });

    it("calls every other departure an import into Russia or an export", () => {
        expect(rateDirection("БЗ", "РФ")).toBe("Импорт");
        expect(rateDirection("ДЗ", "РФ")).toBe("Импорт");
        expect(rateDirection("БЗ", "ДЗ")).toBe("Экспорт");
        expect(rateDirection("ДЗ", "БЗ")).toBe("Экспорт");
    });
});

describe("rateUsd", () => {
    it("converts a rub rate by DIVIDING by the rate, two decimals", () => {
        expect(rateUsd(83900, 83.9)).toBe(1000);
        expect(rateUsd(1000, 83.9)).toBe(11.92);
        expect(rateUsd(0.84, 83.9)).toBe(0.01);
    });
});

describe("rateNet", () => {
    it("sums the three rub rates and their computed dollars", () => {
        const freight = rateUsd(100, 83.9);
        const ppr = rateUsd(200, 83.9);
        const delivery = rateUsd(300, 83.9);
        expect(rateNetRub(100, 200, 300)).toBe(600);
        expect(rateNetUsd(freight, ppr, delivery)).toBe(7.15);
    });
});

describe("rateDirectionPreview", () => {
    const markets: Record<string, string> = { RU: "РФ", BY: "БЗ", DE: "ДЗ" };
    const marketOf = (countryId: string) => markets[countryId];

    it("clears the direction when both countries are cleared", () => {
        expect(rateDirectionPreview("", "", marketOf)).toBe("");
    });

    it("still previews the direction while a country is chosen", () => {
        expect(rateDirectionPreview("RU", "DE", marketOf)).toBe("ДЗ");
        expect(rateDirectionPreview("RU", "", marketOf)).toBe("ДЗ");
        expect(rateDirectionPreview("", "RU", marketOf)).toBe("Импорт");
    });
});
