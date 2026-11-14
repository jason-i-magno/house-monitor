// Package hub fans each house's new readings out to the pages open on it, as Server-Sent Events.
package hub

import (
	"encoding/json"
	"sync"

	"github.com/jason-i-magno/house-monitor/cloud/house"
)

// Buffer is how many messages a page can fall behind by before it's dropped.
const Buffer = 1000

// A Client is one open page's stream.
type Client struct {
	house    string
	messages chan []byte
	lagged   chan struct{}
}

// Messages are the client's events, each a whole Server-Sent Events message, ready to write.
func (c *Client) Messages() <-chan []byte { return c.messages }

// Lagged is closed when the client fell more than Buffer messages behind and was dropped. Its stream should end
// then: the page reconnects, and reloads the house and its history to fill in what it missed, so it ends up whole
// rather than quietly missing readings.
func (c *Client) Lagged() <-chan struct{} { return c.lagged }

// A Hub holds the clients open on each house.
type Hub struct {
	mu      sync.Mutex
	clients map[string]map[*Client]struct{} // by house id
}

func New() *Hub {
	return &Hub{clients: make(map[string]map[*Client]struct{})}
}

// Subscribe opens a stream of a house's new readings. Unsubscribe it when the page goes.
func (h *Hub) Subscribe(houseID string) *Client {
	c := &Client{houseID, make(chan []byte, Buffer), make(chan struct{})}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.clients[houseID] == nil {
		h.clients[houseID] = make(map[*Client]struct{})
	}
	h.clients[houseID][c] = struct{}{}
	return c
}

func (h *Hub) Unsubscribe(c *Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.remove(c)
}

func (h *Hub) remove(c *Client) {
	delete(h.clients[c.house], c)
	if len(h.clients[c.house]) == 0 {
		delete(h.clients, c.house)
	}
}

// Publish sends readings to every page open on the house, in the order given, as the page takes them: each a plain
// message of its JSON. It never waits on a page: one whose buffer is full is dropped instead (Client.Lagged).
func (h *Hub) Publish(houseID string, readings []house.Reading) {
	if len(readings) == 0 {
		return
	}
	messages := make([][]byte, len(readings)) // encoded once, however many pages are open
	for i, r := range readings {
		data, err := json.Marshal(r)
		if err != nil {
			panic(err) // a Reading always encodes
		}
		messages[i] = append(append([]byte("data: "), data...), "\n\n"...)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	for c := range h.clients[houseID] {
		if !send(c, messages) {
			h.remove(c)
			close(c.lagged)
		}
	}
}

// send queues the messages for c, or reports false if its buffer fills first.
func send(c *Client, messages [][]byte) bool {
	for _, m := range messages {
		select {
		case c.messages <- m:
		default:
			return false
		}
	}
	return true
}

// Clients is how many pages are open on the house.
func (h *Hub) Clients(houseID string) int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.clients[houseID])
}
