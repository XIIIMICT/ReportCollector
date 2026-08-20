"use client";

import {
    AlertDialog,
    Button,
    InputGroup,
    Label,
    Modal,
    Spinner,
    TextField,
    ToggleButton,
    ToggleButtonGroup,
} from "@heroui/react";
import {
    Check,
    Computer,
    Eye,
    EyeClosed,
    HalfMoon,
    Key as KeyIcon,
    LogOut,
    SunLight,
    User,
} from "iconoir-react";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useState, type Key } from "react";

import {
    updateProfile,
    type CurrentUser,
    type ThemePreference,
} from "@/shared/api/report-collector";

type ProfileModalProps = {
    isOpen: boolean;
    user: CurrentUser;
    onOpenChange: (open: boolean) => void;
    onProfileSaved: (user: CurrentUser) => void;
};

type Confirmation = "save" | "logout" | null;

const THEME_OPTIONS: Array<{
    icon: typeof Computer;
    id: ThemePreference;
    label: string;
    shortLabel: string;
}> = [
    { icon: Computer, id: "system", label: "Системная тема", shortLabel: "Системная" },
    { icon: SunLight, id: "light", label: "Светлая тема", shortLabel: "Светлая" },
    { icon: HalfMoon, id: "dark", label: "Тёмная тема", shortLabel: "Тёмная" },
];

export function ProfileModal({ isOpen, user, onOpenChange, onProfileSaved }: ProfileModalProps) {
    const { setTheme, theme } = useTheme();
    const [selectedTheme, setSelectedTheme] = useState<ThemePreference>(user.theme);
    const [newPassword, setNewPassword] = useState("");
    const [repeatPassword, setRepeatPassword] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [confirmation, setConfirmation] = useState<Confirmation>(null);
    const [isPending, setIsPending] = useState(false);

    useEffect(() => {
        if (isOpen) {
            setSelectedTheme(user.theme);
            return;
        }
        setNewPassword("");
        setRepeatPassword("");
        setError(null);
        setConfirmation(null);
    }, [isOpen, user.theme]);

    const themeChanged = selectedTheme !== user.theme;
    const hasPasswordInput = Boolean(newPassword || repeatPassword);
    const hasChanges = themeChanged || hasPasswordInput;

    function handleThemeChange(key: Key) {
        const next = String(key) as ThemePreference;
        setSelectedTheme(next);
        setTheme(next);
    }

    function requestSave() {
        if (hasPasswordInput && newPassword.length < 8) {
            return setError("Новый пароль должен содержать не менее 8 символов.");
        }
        if (hasPasswordInput && newPassword !== repeatPassword) {
            return setError("Введённые пароли не совпадают.");
        }
        setError(null);
        if (hasPasswordInput) setConfirmation("save");
        else void saveChanges(false);
    }

    async function saveChanges(changePassword: boolean) {
        setIsPending(true);
        try {
            const updated = await updateProfile({
                theme: themeChanged ? selectedTheme : undefined,
                password: changePassword ? newPassword : undefined,
            });
            setTheme(updated.theme);
            onProfileSaved(updated);
            setNewPassword("");
            setRepeatPassword("");
            setConfirmation(null);
            onOpenChange(false);
        } catch (reason) {
            setError(profileErrorMessage(reason));
            setConfirmation(null);
        } finally {
            setIsPending(false);
        }
    }

    async function confirm() {
        if (confirmation === "save") return saveChanges(true);
        if (confirmation !== "logout") return;
        window.location.assign("/api/auth/logout");
    }

    return (
        <>
            <Modal.Backdrop isOpen={isOpen} onOpenChange={onOpenChange}>
                <Modal.Container>
                    <Modal.Dialog className="profile-modal">
                        <Modal.CloseTrigger />
                        <Modal.Header className="profile-modal__header">
                            <User aria-hidden="true" />
                            <Modal.Heading className="profile-modal__title">Профиль</Modal.Heading>
                        </Modal.Header>
                        <Modal.Body className="profile-modal__body">
                            <ThemeSelector
                                value={theme ?? selectedTheme}
                                onChange={handleThemeChange}
                            />
                            <TextField fullWidth value={user.name ?? ""} isDisabled>
                                <Label>ФИО</Label>
                                <InputGroup fullWidth variant="secondary">
                                    <InputGroup.Input autoComplete="name" />
                                </InputGroup>
                            </TextField>
                            <TextField fullWidth value={user.email ?? ""} isDisabled>
                                <Label>Email</Label>
                                <InputGroup fullWidth variant="secondary">
                                    <InputGroup.Input autoComplete="username" />
                                </InputGroup>
                            </TextField>
                            <PasswordField
                                label="Новый пароль"
                                value={newPassword}
                                autoComplete="new-password"
                                isDisabled={isPending}
                                onChange={setNewPassword}
                            />
                            <PasswordField
                                label="Повторить новый пароль"
                                value={repeatPassword}
                                autoComplete="new-password"
                                isDisabled={isPending}
                                onChange={setRepeatPassword}
                            />
                            {error ? (
                                <div className="form-error" role="alert">
                                    {error}
                                </div>
                            ) : null}
                        </Modal.Body>
                        <Modal.Footer>
                            <Button
                                variant="danger"
                                onPress={() => setConfirmation("logout")}
                                isDisabled={isPending}
                            >
                                <LogOut />
                                Выйти
                            </Button>
                            <Button
                                variant="primary"
                                onPress={requestSave}
                                isDisabled={!hasChanges || isPending}
                            >
                                {isPending ? <Spinner color="current" size="sm" /> : <Check />}
                                Сохранить
                            </Button>
                        </Modal.Footer>
                    </Modal.Dialog>
                </Modal.Container>
            </Modal.Backdrop>

            <AlertDialog
                isOpen={confirmation !== null}
                onOpenChange={(open) => !open && !isPending && setConfirmation(null)}
            >
                <AlertDialog.Backdrop isDismissable={!isPending}>
                    <AlertDialog.Container>
                        <AlertDialog.Dialog>
                            <AlertDialog.Header>
                                <AlertDialog.Icon status="warning">
                                    {confirmation === "logout" ? <LogOut /> : <KeyIcon />}
                                </AlertDialog.Icon>
                                <AlertDialog.Heading>
                                    {confirmation === "logout"
                                        ? "Выйти из приложения?"
                                        : "Сохранить новый пароль?"}
                                </AlertDialog.Heading>
                            </AlertDialog.Header>
                            <AlertDialog.Body>
                                {confirmation === "logout"
                                    ? "Сессия будет завершена, потребуется войти заново."
                                    : "Новый пароль начнёт действовать сразу после сохранения."}
                            </AlertDialog.Body>
                            <AlertDialog.Footer>
                                <Button
                                    variant="tertiary"
                                    onPress={() => setConfirmation(null)}
                                    isDisabled={isPending}
                                >
                                    Отмена
                                </Button>
                                <Button
                                    variant={confirmation === "logout" ? "danger" : "primary"}
                                    onPress={() => void confirm()}
                                    isDisabled={isPending}
                                >
                                    {isPending ? <Spinner color="current" size="sm" /> : null}
                                    {confirmation === "logout" ? "Выйти" : "Сохранить"}
                                </Button>
                            </AlertDialog.Footer>
                        </AlertDialog.Dialog>
                    </AlertDialog.Container>
                </AlertDialog.Backdrop>
            </AlertDialog>
        </>
    );
}

function ThemeSelector({ value, onChange }: { value: string; onChange: (key: Key) => void }) {
    // Stable identity: an inline `new Set(...)` changes on every render and
    // loops HeroUI's selection sync ("Maximum update depth exceeded").
    const selectedKeys = useMemo(() => new Set([value]), [value]);

    return (
        <div className="theme-selector">
            <Label>Тема оформления</Label>
            <ToggleButtonGroup
                className="theme-selector__group"
                fullWidth
                isDisabled={false}
                selectionMode="single"
                selectedKeys={selectedKeys}
                onSelectionChange={(keys) => {
                    const selected = [...keys][0];
                    if (selected !== undefined) onChange(selected);
                }}
            >
                {THEME_OPTIONS.map((option, index) => {
                    const Icon = option.icon;

                    return (
                        <ToggleButton aria-label={option.label} id={option.id} key={option.id}>
                            {index > 0 ? <ToggleButtonGroup.Separator /> : null}
                            <Icon className="size-4" aria-hidden />
                            {option.shortLabel}
                        </ToggleButton>
                    );
                })}
            </ToggleButtonGroup>
        </div>
    );
}

function PasswordField({
    label,
    value,
    onChange,
    autoComplete,
    isDisabled,
}: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    autoComplete: string;
    isDisabled: boolean;
}) {
    const [isVisible, setIsVisible] = useState(false);

    return (
        <TextField
            fullWidth
            className="password-field"
            value={value}
            variant="secondary"
            isDisabled={isDisabled}
            onChange={onChange}
        >
            <Label>{label}</Label>
            <InputGroup fullWidth variant="secondary">
                <InputGroup.Input
                    autoComplete={autoComplete}
                    type={isVisible ? "text" : "password"}
                />
                <InputGroup.Suffix className="pe-0">
                    <Button
                        isIconOnly
                        size="sm"
                        type="button"
                        variant="ghost"
                        aria-label={isVisible ? "Скрыть пароль" : "Показать пароль"}
                        onPress={() => setIsVisible((visible) => !visible)}
                    >
                        {isVisible ? <EyeClosed aria-hidden /> : <Eye aria-hidden />}
                    </Button>
                </InputGroup.Suffix>
            </InputGroup>
        </TextField>
    );
}

function profileErrorMessage(reason: unknown): string {
    if (reason instanceof Error) return reason.message;
    if (typeof reason === "string") return reason;
    return "Не удалось выполнить действие.";
}
