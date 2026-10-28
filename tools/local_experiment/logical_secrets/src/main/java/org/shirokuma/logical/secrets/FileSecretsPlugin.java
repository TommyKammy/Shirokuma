package org.shirokuma.logical.secrets;

import io.airlift.spi.secrets.SecretProviderFactory;
import io.airlift.spi.secrets.SecretsPlugin;
import java.util.List;

public final class FileSecretsPlugin implements SecretsPlugin {
    @Override
    public List<SecretProviderFactory> getSecretProviderFactories() {
        return List.of(new FileSecretProviderFactory());
    }
}
