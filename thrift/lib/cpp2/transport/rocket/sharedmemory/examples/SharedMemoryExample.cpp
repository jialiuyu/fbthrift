/*
 * Example usage of SharedMemoryRocketChannel
 *
 * This demonstrates how to use the shared memory transport
 * with Thrift Rocket protocol.
 */

#include <folly/init/Init.h>
#include <folly/io/async/EventBase.h>
#include <thrift/lib/cpp2/transport/rocket/sharedmemory/SharedMemoryRocketChannel.h>

// Assuming you have a generated Thrift service like this:
// class MyServiceClient : public apache::thrift::GeneratedAsyncClient { ... }

void runClientExample(folly::EventBase& evb) {
  using namespace apache::thrift::rocket::sharedmemory;

  // Configuration
  SharedMemoryRocketChannelConfig config;
  config.dataRegionSize = 4 * 1024 * 1024;  // 4MB
  config.shmNamePrefix = "/myapp_shm_";
  config.connectTimeout = std::chrono::milliseconds(5000);
  config.rpcTimeout = std::chrono::milliseconds(60000);

  // Create channel
  auto channel = SharedMemoryRocketChannel::newChannel(
      &evb,
      "localhost",
      12345,  // server port
      config);

  if (!channel) {
    LOG(ERROR) << "Failed to create shared memory channel";
    return;
  }

  LOG(INFO) << "Shared memory channel created successfully";

  // Create Thrift client with the channel
  // MyServiceClient client(std::move(channel));

  // Make RPC calls as usual
  // auto response = client.co_methodName(request);

  // Get statistics
  auto stats = channel->getStats();
  LOG(INFO) << "Stats: written=" << stats.bytesWritten
            << ", read=" << stats.bytesRead
            << ", notifications_sent=" << stats.notificationSent
            << ", notifications_recv=" << stats.notificationReceived;
}

// Server example
void runServerExample(folly::EventBase& evb) {
  using namespace apache::thrift::rocket::sharedmemory;

  SharedMemoryRocketChannelConfig config;
  config.dataRegionSize = 4 * 1024 * 1024;
  config.shmNamePrefix = "/myapp_shm_";

  auto handler = std::make_unique<SharedMemoryRocketServerHandler>(&evb, config);

  // You would typically integrate this with ThriftServer or
  // a custom socket acceptor that:
  // 1. Accepts TCP connections
  // 2. Passes each connection to handler->handleConnection()
  // 3. Uses the returned SharedMemoryTransport for RPC handling

  LOG(INFO) << "Server handler created";
}

int main(int argc, char** argv) {
  folly::init(&argc, &argv);

  folly::EventBase evb;

  // Run client or server based on arguments
  if (argc > 1 && std::string(argv[1]) == "--server") {
    runServerExample(evb);
  } else {
    runClientExample(evb);
  }

  return 0;
}
