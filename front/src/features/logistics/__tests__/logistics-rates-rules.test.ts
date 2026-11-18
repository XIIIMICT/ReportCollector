import { describe, expect, it } from "bun:test";

import { rateDirection, rateDirectionPreview, rateUsd } from "../logistics-rates-rules";

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
    it("converts a rub rate at the configured rate with two decimals", () => {
        expect(rateUsd(1000, 83.9)).toBe(83900);
        expect(rateUsd(1750, 83.9)).toBe(146825);
        expect(rateUsd(0.01, 83.9)).toBe(0.84);
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
