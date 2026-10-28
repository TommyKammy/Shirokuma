package org.shirokuma.logical.secrets;

import io.airlift.spi.secrets.SecretProvider;
import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

/** Reads each immutable reference afresh, including references added after startup. */
public final class FileSecretProvider implements SecretProvider {
    static final Path CREDENTIALS = Path.of("/etc/trino-private-secrets/credentials.properties");

    static void validateReference(String reference) {
        if (reference == null || !reference.matches("[A-Za-z0-9][A-Za-z0-9_.-]{0,191}")) {
            throw new IllegalArgumentException("Invalid secret reference");
        }
    }

    @Override
    public String resolveSecretValue(String reference) {
        validateReference(reference);
        Properties values = new Properties();
        try (Reader reader = Files.newBufferedReader(CREDENTIALS, StandardCharsets.UTF_8)) {
            values.load(reader);
        }
        catch (IOException | IllegalArgumentException exception) {
            throw new IllegalStateException("Secret store unavailable");
        }
        String value = values.getProperty(reference);
        if (value == null || value.isEmpty()) {
            throw new IllegalArgumentException("Secret reference unavailable");
        }
        return value;
    }
}
