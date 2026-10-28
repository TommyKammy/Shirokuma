package org.shirokuma.logical.secrets;

import io.airlift.spi.secrets.SecretProvider;
import io.airlift.spi.secrets.SecretProviderFactory;
import java.util.Map;

public final class FileSecretProviderFactory implements SecretProviderFactory {
    @Override
    public String getName() {
        return "file";
    }

    @Override
    public SecretProvider createSecretProvider(Map<String, String> config) {
        if (!config.isEmpty()) {
            throw new IllegalArgumentException("File secret provider accepts no configuration");
        }
        return new FileSecretProvider();
    }
}
