import { afterEach, expect, spyOn, test } from "bun:test";

import { Button } from "@heroui/react";
import { cleanup, render } from "@testing-library/react";

import { HoverDropdown } from "../hover-dropdown";

afterEach(cleanup);

test("HoverDropdown polls nothing and removes its pointer listener on unmount", () => {
    const interval = spyOn(window, "setInterval");
    const removeListener = spyOn(window, "removeEventListener");
    const { unmount } = render(
        <HoverDropdown
            trigger={({ ref, onHoverStart, onPress, isOpen }) => (
                <Button
                    ref={ref as React.Ref<HTMLButtonElement>}
                    onHoverStart={onHoverStart}
                    onPress={onPress}
                    aria-label="Меню"
                    aria-expanded={isOpen}
                >
                    Меню
                </Button>
            )}
            content={<div>Пункт</div>}
        />,
    );
    // The removed 60 ms loop must not come back as another interval: geometry
    // checks are driven by pointermove + one coalesced RAF + one grace timeout.
    expect(interval).not.toHaveBeenCalled();
    unmount();
    expect(removeListener).toHaveBeenCalledWith("pointermove", expect.any(Function));
});
