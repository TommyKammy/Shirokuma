import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Pattern;
import org.apache.iceberg.BaseTable;
import org.apache.iceberg.CatalogProperties;
import org.apache.iceberg.CatalogUtil;
import org.apache.iceberg.DataFile;
import org.apache.iceberg.DataFiles;
import org.apache.iceberg.FileFormat;
import org.apache.iceberg.FileScanTask;
import org.apache.iceberg.PartitionSpec;
import org.apache.iceberg.Schema;
import org.apache.iceberg.Table;
import org.apache.iceberg.TableMetadata;
import org.apache.iceberg.TableProperties;
import org.apache.iceberg.aws.AwsClientProperties;
import org.apache.iceberg.aws.s3.S3FileIO;
import org.apache.iceberg.aws.s3.S3FileIOProperties;
import org.apache.iceberg.avro.Avro;
import org.apache.iceberg.catalog.Namespace;
import org.apache.iceberg.catalog.TableIdentifier;
import org.apache.iceberg.data.GenericRecord;
import org.apache.iceberg.data.Record;
import org.apache.iceberg.data.avro.DataReader;
import org.apache.iceberg.data.avro.DataWriter;
import org.apache.iceberg.io.CloseableIterable;
import org.apache.iceberg.io.FileAppender;
import org.apache.iceberg.rest.RESTCatalog;
import org.apache.iceberg.rest.auth.OAuth2Properties;
import org.apache.iceberg.types.Types;

/** Idempotently materialize and verify the bounded L1 Iceberg fixture. */
public final class IcebergBootstrap {
  private static final ObjectMapper JSON = new ObjectMapper();
  private static final HttpClient HTTP =
      HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
  private static final Pattern ICEBERG_NAME = Pattern.compile("[a-z][a-z0-9_]{0,62}");
  private static final String CATALOG = nameSetting("shirokuma.catalog", "shirokuma_l1");
  private static final String NAMESPACE = nameSetting("shirokuma.namespace", "smoke");
  private static final String TABLE = nameSetting("shirokuma.table", "fixture_v1");
  private static final String BASE_LOCATION =
      locationSetting("shirokuma.base-location", "s3://shirokuma-lakehouse/l1");
  private static final String FIXTURE_MARKER = "shirokuma.dev/fixture-sha256";
  private static final String FIXTURE_VERSION = "shirokuma.dev/fixture-version";
  private static final List<Row> FIXTURE =
      List.of(new Row(1L, "shirokuma"), new Row(2L, "polaris"));
  private static final Schema SCHEMA =
      new Schema(
          Types.NestedField.required(1, "id", Types.LongType.get()),
          Types.NestedField.required(2, "name", Types.StringType.get()));
  private static final PartitionSpec SPEC = PartitionSpec.unpartitioned();

  private IcebergBootstrap() {}

  public static void main(String[] args) {
    Environment environment = null;
    try {
      if (args.length == 1 && "--self-test".equals(args[0])) {
        emitSummary("self-test", false, 0L, FIXTURE.size());
        return;
      }
      environment = Environment.load();
      if (args.length == 1 && "--cleanup".equals(args[0])) {
        int removed = cleanup(environment);
        emitCleanupSummary(removed);
        return;
      }
      if (args.length != 0) {
        throw new BootstrapException("unsupported arguments");
      }
      Result result = run(environment);
      emitSummary("passed", result.created(), result.snapshotId(), result.rowCount());
    } catch (Exception error) {
      String detail =
          error instanceof BootstrapException
              ? Objects.toString(error.getMessage(), "failed")
              : "operation failed";
      System.err.println(
          "iceberg-bootstrap: "
              + sanitize(
                  error.getClass().getSimpleName() + ": " + detail,
                  environment)
              + safeStack(error));
      System.exit(1);
    }
  }

  private static Result run(Environment environment) throws Exception {
    ensureManagementCatalog(environment);
    Map<String, String> properties = catalogProperties(environment);

    try (RESTCatalog catalog = new RESTCatalog()) {
      catalog.initialize("shirokuma", properties);
      Namespace namespace = Namespace.of(NAMESPACE);
      if (!catalog.namespaceExists(namespace)) {
        Map<String, String> namespaceProperties = new HashMap<>();
        namespaceProperties.put(
            "shirokuma.dev/managed-by", "gitops-bootstrap");
        catalog.createNamespace(namespace, namespaceProperties);
      }

      TableIdentifier identifier = TableIdentifier.of(namespace, TABLE);
      boolean created = false;
      Table table;
      if (!catalog.tableExists(identifier)) {
        Map<String, String> tableProperties = new HashMap<>();
        tableProperties.put(TableProperties.FORMAT_VERSION, "2");
        tableProperties.put(
            TableProperties.DEFAULT_FILE_FORMAT,
            FileFormat.AVRO.name().toLowerCase(Locale.ROOT));
        tableProperties.put(FIXTURE_VERSION, "1");
        table =
            catalog.createTable(
                identifier,
                SCHEMA,
                SPEC,
                tableProperties);
        created = true;
      } else {
        table = catalog.loadTable(identifier);
      }

      validateTableContract(table);
      String fixtureDigest = fixtureDigest();
      if (table.currentSnapshot() == null) {
        writeFixture(table, fixtureDigest);
        table.refresh();
      } else if (!fixtureDigest.equals(table.properties().get(FIXTURE_MARKER))) {
        throw new BootstrapException("existing table is not the reviewed fixture");
      }

      List<TableIdentifier> listed = catalog.listTables(namespace);
      if (!listed.contains(identifier)) {
        throw new BootstrapException("table is absent from catalog listing");
      }
      table.refresh();
      long snapshotId =
          Objects.requireNonNull(table.currentSnapshot(), "current snapshot is absent")
              .snapshotId();
      int rowCount = verifyFixtureRead(table, fixtureDigest);
      return new Result(created, snapshotId, rowCount);
    }
  }

  private static Map<String, String> catalogProperties(Environment environment) {
    Map<String, String> properties = new HashMap<>();
    properties.put(CatalogProperties.URI, environment.polarisUri() + "/api/catalog");
    properties.put(CatalogProperties.WAREHOUSE_LOCATION, CATALOG);
    properties.put(
        OAuth2Properties.CREDENTIAL,
        environment.clientId() + ":" + environment.clientSecret());
    properties.put(OAuth2Properties.SCOPE, "PRINCIPAL_ROLE:ALL");
    properties.put("header.Polaris-Realm", environment.realm());
    properties.put(CatalogProperties.FILE_IO_IMPL, S3FileIO.class.getName());
    properties.put(S3FileIOProperties.ENDPOINT, environment.s3Endpoint());
    properties.put(S3FileIOProperties.PATH_STYLE_ACCESS, "true");
    properties.put(S3FileIOProperties.ACCESS_KEY_ID, environment.s3AccessKey());
    properties.put(S3FileIOProperties.SECRET_ACCESS_KEY, environment.s3SecretKey());
    properties.put(S3FileIOProperties.REMOTE_SIGNING_ENABLED, "false");
    properties.put(AwsClientProperties.CLIENT_REGION, environment.s3Region());
    return properties;
  }

  private static int cleanup(Environment environment) throws Exception {
    String token = managementToken(environment);
    URI catalogUri =
        URI.create(environment.polarisUri() + "/api/management/v1/catalogs/" + CATALOG);
    HttpResponse<String> existing = request(catalogUri, "GET", token, environment.realm(), null);
    if (existing.statusCode() == 404) {
      return 0;
    }
    if (existing.statusCode() != 200) {
      throw new BootstrapException("catalog read returned HTTP " + existing.statusCode());
    }
    validateManagementCatalog(JSON.readTree(existing.body()), environment);
    int removed = 0;
    try (RESTCatalog catalog = new RESTCatalog()) {
      catalog.initialize("shirokuma", catalogProperties(environment));
      Namespace namespace = Namespace.of(NAMESPACE);
      TableIdentifier identifier = TableIdentifier.of(namespace, TABLE);
      if (catalog.tableExists(identifier)) {
        Table table = catalog.loadTable(identifier);
        if (!(table instanceof BaseTable baseTable)) {
          throw new BootstrapException("table cleanup cannot access reviewed metadata");
        }
        TableMetadata metadata = baseTable.operations().current();
        if (!catalog.dropTable(identifier, false)) {
          throw new BootstrapException("table cleanup did not report success");
        }
        CatalogUtil.dropTableData(table.io(), metadata);
        removed++;
      }
      if (catalog.namespaceExists(namespace)) {
        if (!catalog.dropNamespace(namespace)) {
          throw new BootstrapException("namespace cleanup did not report success");
        }
        removed++;
      }
    }
    HttpResponse<String> deleted = request(catalogUri, "DELETE", token, environment.realm(), null);
    if (deleted.statusCode() != 204) {
      throw new BootstrapException("catalog cleanup returned HTTP " + deleted.statusCode());
    }
    return removed + 1;
  }

  private static void ensureManagementCatalog(Environment environment) throws Exception {
    String token = managementToken(environment);
    URI catalogUri =
        URI.create(environment.polarisUri() + "/api/management/v1/catalogs/" + CATALOG);
    HttpResponse<String> existing = request(catalogUri, "GET", token, environment.realm(), null);
    if (existing.statusCode() == 200) {
      validateManagementCatalog(JSON.readTree(existing.body()), environment);
      return;
    }
    if (existing.statusCode() != 404) {
      throw new BootstrapException("catalog read returned HTTP " + existing.statusCode());
    }

    ObjectNode storage = JSON.createObjectNode();
    storage.put("storageType", "S3");
    storage.putArray("allowedLocations").add(BASE_LOCATION);
    storage.put("region", environment.s3Region());
    storage.put("endpoint", environment.s3Endpoint());
    storage.put("endpointInternal", environment.s3Endpoint());
    storage.put("stsUnavailable", true);
    storage.put("pathStyleAccess", true);
    ObjectNode catalog = JSON.createObjectNode();
    catalog.put("type", "INTERNAL");
    catalog.put("name", CATALOG);
    catalog.putObject("properties").put("default-base-location", BASE_LOCATION);
    catalog.set("storageConfigInfo", storage);
    ObjectNode body = JSON.createObjectNode();
    body.set("catalog", catalog);
    HttpResponse<String> created =
        request(
            URI.create(environment.polarisUri() + "/api/management/v1/catalogs"),
            "POST",
            token,
            environment.realm(),
            JSON.writeValueAsString(body));
    if (created.statusCode() != 201) {
      throw new BootstrapException("catalog create returned HTTP " + created.statusCode());
    }
    validateManagementCatalog(JSON.readTree(created.body()), environment);
  }

  private static String managementToken(Environment environment) throws Exception {
    String form = "grant_type=client_credentials&scope=PRINCIPAL_ROLE%3AALL";
    String basic =
        Base64.getEncoder()
            .encodeToString(
                (environment.clientId() + ":" + environment.clientSecret())
                    .getBytes(StandardCharsets.UTF_8));
    HttpRequest request =
        HttpRequest.newBuilder(
                URI.create(environment.polarisUri() + "/api/catalog/v1/oauth/tokens"))
            .timeout(Duration.ofSeconds(20))
            .header("Authorization", "Basic " + basic)
            .header("Content-Type", "application/x-www-form-urlencoded")
            .header("Polaris-Realm", environment.realm())
            .POST(HttpRequest.BodyPublishers.ofString(form))
            .build();
    HttpResponse<String> response = HTTP.send(request, HttpResponse.BodyHandlers.ofString());
    JsonNode payload = JSON.readTree(response.body());
    String token = payload.path("access_token").asText("");
    if (response.statusCode() != 200 || token.isEmpty()) {
      throw new BootstrapException("token request failed");
    }
    return token;
  }

  private static HttpResponse<String> request(
      URI uri, String method, String token, String realm, String body) throws Exception {
    HttpRequest.Builder builder =
        HttpRequest.newBuilder(uri)
            .timeout(Duration.ofSeconds(30))
            .header("Authorization", "Bearer " + token)
            .header("Polaris-Realm", realm)
            .header("Content-Type", "application/json");
    builder.method(
        method,
        body == null
            ? HttpRequest.BodyPublishers.noBody()
            : HttpRequest.BodyPublishers.ofString(body));
    return HTTP.send(builder.build(), HttpResponse.BodyHandlers.ofString());
  }

  private static void validateManagementCatalog(JsonNode response, Environment environment) {
    JsonNode catalog = response.has("catalog") ? response.path("catalog") : response;
    JsonNode storage = catalog.path("storageConfigInfo");
    JsonNode allowedLocations = storage.path("allowedLocations");
    if (!CATALOG.equals(catalog.path("name").asText())
        || !"INTERNAL".equals(catalog.path("type").asText())
        || !BASE_LOCATION.equals(catalog.path("properties").path("default-base-location").asText())
        || !"S3".equals(storage.path("storageType").asText())
        || !hasSingleAllowedLocation(allowedLocations)
        || !environment.s3Region().equals(storage.path("region").asText())
        || !environment.s3Endpoint().equals(storage.path("endpoint").asText())
        || !environment.s3Endpoint().equals(storage.path("endpointInternal").asText())
        || !storage.path("stsUnavailable").asBoolean(false)
        || !storage.path("pathStyleAccess").asBoolean(false)) {
      throw new BootstrapException("catalog contract differs from the reviewed state");
    }
  }

  private static boolean hasSingleAllowedLocation(JsonNode allowedLocations) {
    return allowedLocations.isArray()
        && allowedLocations.size() == 1
        && BASE_LOCATION.equals(allowedLocations.get(0).asText());
  }

  private static void validateTableContract(Table table) {
    if (!SCHEMA.sameSchema(table.schema())) {
      throw new BootstrapException("table schema differs from the reviewed state");
    }
    if (!SPEC.equals(table.spec())) {
      throw new BootstrapException("table partition spec differs from the reviewed state");
    }
    if (!(BASE_LOCATION + "/" + NAMESPACE + "/" + TABLE)
        .equals(table.location().replaceAll("/+$", ""))) {
      throw new BootstrapException("table location differs from the reviewed state");
    }
    if (!(table instanceof BaseTable baseTable)
        || baseTable.operations().current().formatVersion() != 2) {
      throw new BootstrapException("table format version differs from the reviewed state");
    }
    if (!"1".equals(table.properties().get(FIXTURE_VERSION))) {
      throw new BootstrapException("table fixture version differs from the reviewed state");
    }
  }

  private static void writeFixture(Table table, String fixtureDigest) throws IOException {
    String fixturePath = table.location().replaceAll("/+$", "") + "/data/fixture-v1.avro";
    long recordCount = 0;
    FileAppender<Record> writer =
        Avro.write(table.io().newOutputFile(fixturePath))
            .schema(SCHEMA)
            .createWriterFunc(DataWriter::create)
            .overwrite()
            .build();
    try (writer) {
      for (Row row : FIXTURE) {
        GenericRecord record = GenericRecord.create(SCHEMA);
        record.setField("id", row.id());
        record.setField("name", row.name());
        writer.add(record);
        recordCount++;
      }
    }
    long fileSize = writer.length();
    org.apache.iceberg.Metrics metrics = writer.metrics();
    DataFile dataFile =
        DataFiles.builder(SPEC)
            .withPath(fixturePath)
            .withFormat(FileFormat.AVRO)
            .withFileSizeInBytes(fileSize)
            .withRecordCount(recordCount)
            .withMetrics(metrics)
            .build();
    table.newAppend().appendFile(dataFile).commit();
    table.updateProperties().set(FIXTURE_MARKER, fixtureDigest).commit();
  }

  @SuppressWarnings("deprecation")
  private static int verifyFixtureRead(Table table, String fixtureDigest) throws IOException {
    if (!fixtureDigest.equals(table.properties().get(FIXTURE_MARKER))) {
      throw new BootstrapException("fixture marker does not match reviewed rows");
    }
    List<Row> actual = new ArrayList<>();
    int dataFiles = 0;
    try (CloseableIterable<FileScanTask> tasks = table.newScan().planFiles()) {
      for (FileScanTask task : tasks) {
        dataFiles++;
        try (CloseableIterable<Record> records =
            Avro.read(table.io().newInputFile(task.file().location()))
                .project(SCHEMA)
                .createReaderFunc(fileSchema -> DataReader.create(SCHEMA, fileSchema))
                .build()) {
          for (Record record : records) {
            actual.add(
                new Row(
                    ((Number) record.getField("id")).longValue(),
                    (String) record.getField("name")));
          }
        }
      }
    }
    actual.sort(Comparator.comparingLong(Row::id));
    if (dataFiles != 1 || !FIXTURE.equals(actual)) {
      throw new BootstrapException("fixture readback differs from reviewed rows");
    }
    return actual.size();
  }

  private static String fixtureDigest() throws Exception {
    MessageDigest digest = MessageDigest.getInstance("SHA-256");
    for (Row row : FIXTURE) {
      digest.update((row.id() + "\t" + row.name() + "\n").getBytes(StandardCharsets.UTF_8));
    }
    return java.util.HexFormat.of().formatHex(digest.digest());
  }

  private static void emitSummary(String result, boolean created, long snapshotId, int rowCount)
      throws Exception {
    ObjectNode summary = JSON.createObjectNode();
    summary.put("catalog", CATALOG);
    summary.put("namespace", NAMESPACE);
    summary.put("table", TABLE);
    summary.put("result", result);
    summary.put("created", created);
    summary.put("snapshot_id", snapshotId);
    summary.put("data_files", "self-test".equals(result) ? 0 : 1);
    summary.put("rows", rowCount);
    summary.put("credential_material_retained", false);
    System.out.println(JSON.writeValueAsString(summary));
  }

  private static void emitCleanupSummary(int removed) throws Exception {
    ObjectNode summary = JSON.createObjectNode();
    summary.put("catalog", CATALOG);
    summary.put("namespace", NAMESPACE);
    summary.put("table", TABLE);
    summary.put("result", "cleanup-passed");
    summary.put("resources_removed", removed);
    summary.put("credential_material_retained", false);
    System.out.println(JSON.writeValueAsString(summary));
  }

  private static String requireEnvironment(String name) {
    String value = System.getenv(name);
    if (value == null || value.isBlank() || value.indexOf('\n') >= 0 || value.indexOf('\r') >= 0) {
      throw new BootstrapException("required environment variable is missing or invalid: " + name);
    }
    return value;
  }

  private static String nameSetting(String name, String fallback) {
    String value = System.getProperty(name, fallback);
    if (!ICEBERG_NAME.matcher(value).matches()) {
      throw new BootstrapException("invalid Iceberg identifier setting: " + name);
    }
    return value;
  }

  private static String locationSetting(String name, String fallback) {
    String value = System.getProperty(name, fallback);
    if (!value.matches("s3://shirokuma-lakehouse/[a-z0-9/_-]+")
        || value.substring("s3://".length()).contains("//")
        || value.contains("..")
        || value.endsWith("/")) {
      throw new BootstrapException("invalid object-storage location setting: " + name);
    }
    return value;
  }

  private static String sanitize(String message, Environment environment) {
    if (environment == null) {
      return message;
    }
    String safe = message;
    for (String secret : List.of(
        environment.clientSecret(), environment.s3AccessKey(), environment.s3SecretKey())) {
      safe = safe.replace(secret, "<redacted>");
    }
    return safe;
  }

  private static String safeStack(Exception error) {
    StringBuilder stack = new StringBuilder();
    StackTraceElement[] elements = error.getStackTrace();
    for (int index = 0; index < Math.min(elements.length, 8); index++) {
      StackTraceElement element = elements[index];
      stack
          .append("\n  at ")
          .append(element.getClassName())
          .append('.')
          .append(element.getMethodName())
          .append(':')
          .append(element.getLineNumber());
    }
    return stack.toString();
  }

  private record Row(long id, String name) {}

  private record Result(boolean created, long snapshotId, int rowCount) {}

  private record Environment(
      String polarisUri,
      String clientId,
      String clientSecret,
      String realm,
      String s3Endpoint,
      String s3Region,
      String s3AccessKey,
      String s3SecretKey) {
    static Environment load() {
      return new Environment(
          requireEnvironment("POLARIS_URI"),
          requireEnvironment("POLARIS_CLIENT_ID"),
          requireEnvironment("POLARIS_CLIENT_SECRET"),
          requireEnvironment("POLARIS_REALM"),
          requireEnvironment("S3_ENDPOINT"),
          requireEnvironment("AWS_REGION"),
          requireEnvironment("AWS_ACCESS_KEY_ID"),
          requireEnvironment("AWS_SECRET_ACCESS_KEY"));
    }
  }

  private static final class BootstrapException extends RuntimeException {
    BootstrapException(String message) {
      super(message);
    }
  }
}
