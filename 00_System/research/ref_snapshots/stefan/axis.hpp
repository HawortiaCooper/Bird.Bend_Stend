/**
 * axis.hpp — step generation core with a trapezoidal velocity profile.
 *
 * The class is parameterised by a hardware policy Hw. On the board that is the
 * TIM2 and GPIO registers; in tests it is a model that integrates time. The
 * policy is a template parameter, so the calls inline and no vtables appear:
 * on the hot interrupt path the code is the same as it was in C.
 *
 * Hw must provide these static methods:
 *      timerStart(uint32_t period_ticks)
 *      timerStop()
 *      timerSetPeriod(uint32_t period_ticks)
 *      timerSetPulseWidth(uint32_t ticks)
 *      setDirectionPin(bool level)
 *      setEnablePin(bool level)
 *      bool limitMinRaw() / limitMaxRaw() / estopRaw()
 *      dirSetupDelay()
 *      static constexpr uint32_t kTimerHz
 */
#pragma once

#include "mcu.hpp"
#include "units.hpp"

#include <cmath>
#include <cstdint>

namespace stepctl {

/** Status flags. Values match the protocol of the C version. */
struct Flag {
    static constexpr std::uint32_t Enabled = 0x01;
    static constexpr std::uint32_t Homed   = 0x02;
    static constexpr std::uint32_t LimMin  = 0x04;
    static constexpr std::uint32_t LimMax  = 0x08;
    static constexpr std::uint32_t Estop   = 0x10;
    static constexpr std::uint32_t SoftLim = 0x20;
};

struct Event {
    static constexpr std::uint32_t Done  = 0x01;
    static constexpr std::uint32_t Limit = 0x02;
    static constexpr std::uint32_t Estop = 0x04;
    static constexpr std::uint32_t Homed = 0x08;
    static constexpr std::uint32_t Fault = 0x10;
};

enum class State : std::uint8_t { Idle = 0, Accel, Run, Decel, Homing, Fault };
enum class Mode  : std::uint8_t { Position = 0, Velocity };

struct Config {
    float steps_per_mm    = 640.0f;
    float vmax_mms        = 20.0f;
    float accel_mms2      = 200.0f;
    float home_v_mms      = 8.0f;
    float home_backoff_mm = 3.0f;
    std::uint16_t pulse_us = 3;
    bool dir_invert  = false;
    bool ena_invert  = true;
    bool lim_invert  = false;
    bool soft_limits = false;
    /* Hardware limit switches. Disabled by default: with nothing wired to
       PB0/PB1 a floating input would trip a fault at random. Turn back on
       with CFG LIMITS 1 once the switches are in place — homing needs it. */
    bool limits_enabled = false;
    float soft_min_mm = 0.0f;
    float soft_max_mm = 300.0f;
};

/** Status snapshot. Numeric fields are already scaled to integers so that the
 *  protocol can format them without the float build of printf. */
struct Status {
    State state{State::Idle};
    Steps pos{};
    Steps target{};
    std::int64_t pos_mm_e4{};   // millimetres x 10^4
    std::int64_t tgt_mm_e4{};
    std::int64_t vel_mms_e3{};  // mm/s x 10^3, signed
    std::uint32_t flags{};
    std::uint32_t uptime_ms{};
};

template <class Hw>
class Axis {
public:
    static constexpr std::uint32_t kTimerHz = Hw::kTimerHz;

    void init()
    {
        cfg_ = Config{};
        applyConfig();
        enable(false);
    }

    Config&       config()       { return cfg_; }
    const Config& config() const { return cfg_; }
    const Scale&  scale()  const { return scale_; }

    /** Recomputes c0 / c_min after a configuration change. */
    void applyConfig()
    {
        if (cfg_.steps_per_mm < 0.001f) cfg_.steps_per_mm = 1.0f;
        if (cfg_.vmax_mms     < 0.001f) cfg_.vmax_mms     = 1.0f;
        if (cfg_.accel_mms2   < 0.001f) cfg_.accel_mms2   = 1.0f;
        if (cfg_.pulse_us == 0)         cfg_.pulse_us     = 3;

        scale_.set(cfg_.steps_per_mm);

        const float v_steps = cfg_.vmax_mms   * cfg_.steps_per_mm;
        const float a_steps = cfg_.accel_mms2 * cfg_.steps_per_mm;

        double cmin = std::ceil(static_cast<double>(kTimerHz) / v_steps);
        double c0   = 0.676 * static_cast<double>(kTimerHz) * std::sqrt(2.0 / a_steps);

        if (cmin < 4.0)   cmin = 4.0;
        if (c0 < cmin)    c0 = cmin;
        if (c0 > 4.0e9)   c0 = 4.0e9;

        std::uint32_t pulse = static_cast<std::uint32_t>(cfg_.pulse_us) * (kTimerHz / 1000000u);
        if (pulse < 2) pulse = 2;
        if (pulse > static_cast<std::uint32_t>(cmin) / 2)
            pulse = static_cast<std::uint32_t>(cmin) / 2;
        pulse_ticks_ = pulse;

        {
            CriticalSection cs;
            c_min_ = static_cast<std::uint32_t>(cmin);
            c0_    = static_cast<std::uint32_t>(c0);
        }
        Hw::timerSetPulseWidth(pulse_ticks_);
    }

    // ----------------------------------------------------------- commands

    void enable(bool on)
    {
        Hw::setEnablePin(cfg_.ena_invert ? !on : on);
        if (on) flags_ |= Flag::Enabled;
        else    flags_ &= ~Flag::Enabled;
        if (!on) halt();
    }

    bool enabled() const { return (flags_ & Flag::Enabled) != 0; }

    /** Busy as seen by external commands: moving OR homing not yet finished. */
    bool busy() const { return motionBusy() || home_phase_ != 0; }

    [[nodiscard]] Error moveAbsolute(Mm target)
    {
        if (const Error e = precheck(); !ok(e)) return e;
        const Steps t = scale_.toSteps(target);
        if (const Error e = checkSoft(t); !ok(e)) return e;

        if (busy()) {                       // interrupt the current move smoothly
            pending_ = Pending::Position;
            pending_target_ = t;
            stop();
        } else {
            launchPosition(t);
        }
        return Error::Ok;
    }

    [[nodiscard]] Error moveRelative(Mm delta)
    {
        const Steps base = (busy() && mode_ == Mode::Position) ? target_ : pos_;
        return moveAbsolute(Mm{scale_.toMm(base).raw() + delta.raw()});
    }

    [[nodiscard]] Error jog(MmPerSec v)
    {
        if (const Error e = precheck(); !ok(e)) return e;
        if (v.magnitude() > cfg_.vmax_mms + 1e-3f) return Error::Range;

        const std::int8_t d = static_cast<std::int8_t>(v.sign());

        // same axis and direction while moving: change target speed on the fly
        if (busy() && mode_ == Mode::Velocity && dir_ == d && home_phase_ == 0) {
            const std::uint32_t cmin = periodFor(v);
            CriticalSection cs;
            c_min_ = cmin;
            rest_  = 0;
            if (c_ > cmin)      { state_ = State::Accel; }
            else if (c_ < cmin) { state_ = State::Decel; decel_to_stop_ = false;
                                  n_ = -(n_ > 0 ? n_ : 1); }
            return Error::Ok;
        }

        if (busy()) {
            pending_ = Pending::Velocity;
            pending_vel_ = v;
            stop();
        } else {
            applyConfig();
            launchVelocity(v);
        }
        return Error::Ok;
    }

    /** Stops with deceleration following the profile. */
    void stop()
    {
        CriticalSection cs;
        if (state_ == State::Accel || state_ == State::Run) {
            mode_ = Mode::Velocity;
            state_ = State::Decel;
            decel_to_stop_ = true;
            n_ = -(n_ > 0 ? n_ : 1);
            rest_ = 0;
        } else if (state_ == State::Decel) {
            mode_ = Mode::Velocity;
            decel_to_stop_ = true;
        }
    }

    /** Immediate stop. Steps may be lost. */
    void halt()
    {
        CriticalSection cs;
        Hw::timerStop();
        if (state_ != State::Fault) state_ = State::Idle;
        remaining_  = 0;
        pending_    = Pending::None;
        home_phase_ = 0;
    }

    void zero()
    {
        CriticalSection cs;
        pos_ = Steps{};
        target_ = Steps{};
        flags_ |= Flag::Homed;
    }

    void clearFault()
    {
        CriticalSection cs;
        if (state_ == State::Fault) state_ = State::Idle;
        flags_ &= ~(Flag::LimMin | Flag::LimMax | Flag::Estop);
        pending_    = Pending::None;
        home_phase_ = 0;
    }

    [[nodiscard]] Error home()
    {
        if (const Error e = precheck(); !ok(e)) return e;
        if (busy()) return Error::Busy;
        if (!cfg_.limits_enabled) return Error::Limit;   // nothing to home against

        home_phase_ = 1;
        state_ = State::Homing;
        if (limitMin()) {
            home_phase_ = 2;
            launchPosition(pos_ + scale_.toSteps(Mm{cfg_.home_backoff_mm}));
        } else {
            launchVelocity(MmPerSec{-cfg_.home_v_mms});
        }
        return Error::Ok;
    }

    // -------------------------------------------------------- ISR hot path

    /** Called from the timer update interrupt, once per step. */
    void onTimerUpdate()
    {
        pos_ += Steps{static_cast<std::int32_t>(dir_)};

        if (Hw::estopRaw()) {
            Hw::timerStop();
            state_ = State::Fault;
            flags_ |= Flag::Estop;
            events_ |= Event::Estop | Event::Fault;
            return;
        }
        if (directionBlocked(dir_)) {
            Hw::timerStop();
            flags_ |= (dir_ < 0) ? Flag::LimMin : Flag::LimMax;
            events_ |= Event::Limit;
            // while homing, a limit switch is an expected event, not a fault
            state_ = (home_phase_ != 0) ? State::Idle : State::Fault;
            if (state_ == State::Fault) events_ |= Event::Fault;
            return;
        }

        if (mode_ == Mode::Position) {
            if (remaining_) --remaining_;
            if (remaining_ == 0) { finish(Event::Done); return; }
        }

        switch (state_) {
        case State::Accel:
            if (n_ < 1000000) ++n_;
            rampNext();
            if (c_ <= c_min_) { c_ = c_min_; state_ = State::Run; rest_ = 0; }
            break;

        case State::Run:
            c_ = c_min_;
            break;

        case State::Decel:
            ++n_;
            if (n_ >= 0) {
                if (decel_to_stop_) { finish(Event::Done); return; }
                c_ = c_min_; state_ = State::Run; n_ = 0; rest_ = 0;
                break;
            }
            rampNext();
            if (decel_to_stop_) {
                // stopping at c0 applies to velocity mode only; in position
                // mode remaining_ decides, otherwise we stop one step short
                if (mode_ == Mode::Velocity && c_ >= c0_) { finish(Event::Done); return; }
            } else if (c_ >= c_min_) {
                c_ = c_min_; state_ = State::Run; n_ = -n_; rest_ = 0;
            }
            break;

        default:
            Hw::timerStop();
            return;
        }

        // deceleration trigger point for a position move
        if (mode_ == Mode::Position && state_ != State::Decel &&
            n_ > 0 && remaining_ <= static_cast<std::uint32_t>(n_)) {
            state_ = State::Decel;
            decel_to_stop_ = true;
            n_    = -static_cast<std::int32_t>(remaining_);
            rest_ = 0;
        }

        Hw::timerSetPeriod(c_ - 1);
    }

    // --------------------------------------------------------------- service

    void tick1ms() { ++ms_; }

    void service()
    {
        if (Hw::estopRaw() && state_ != State::Fault) {
            halt();
            state_ = State::Fault;
            flags_ |= Flag::Estop;
            events_ |= Event::Estop | Event::Fault;
            enable(false);
        }

        if (limitMin()) flags_ |= Flag::LimMin;
        if (limitMax()) flags_ |= Flag::LimMax;

        homeService();

        if (!busy() && pending_ != Pending::None && state_ != State::Fault) {
            const Pending p = pending_;
            pending_ = Pending::None;
            if (p == Pending::Position) {
                launchPosition(pending_target_);
            } else {
                applyConfig();
                launchVelocity(pending_vel_);
            }
        }
    }

    Status status() const
    {
        Status s;
        State st; Steps pos, tgt; std::uint32_t c; std::int8_t dir; std::uint32_t fl;
        {
            CriticalSection cs;
            st = state_; pos = pos_; c = c_; dir = dir_; fl = flags_;
            tgt = (mode_ == Mode::Position) ? target_ : pos_;
        }
        s.state     = (home_phase_ != 0) ? State::Homing : st;
        s.pos       = pos;
        s.target    = tgt;
        s.pos_mm_e4 = scale_.toMmScaled(pos);
        s.tgt_mm_e4 = scale_.toMmScaled(tgt);
        s.flags     = fl;
        s.uptime_ms = ms_;

        if (st == State::Accel || st == State::Run || st == State::Decel) {
            // mm/s x 1000 = 10^6 * F / (c * steps_per_mm_x1000), integer only
            const std::int64_t den = static_cast<std::int64_t>(c) * scale_.stepsPerMmMilli();
            s.vel_mms_e3 = den ? (1000000LL * kTimerHz / den) * (dir > 0 ? 1 : -1) : 0;
        }
        return s;
    }

    std::uint32_t takeEvents()
    {
        CriticalSection cs;
        const std::uint32_t e = events_;
        events_ = 0;
        return e;
    }

private:
    enum class Pending : std::uint8_t { None = 0, Position, Velocity };

    // ---- helpers ----

    /** Whether the timer is currently running. Deliberately does NOT consider
     *  home_phase_: the homing state machine must see the gaps between its own
     *  phases, otherwise it deadlocks itself on the very first one. */
    bool motionBusy() const
    {
        const State s = state_;
        return s == State::Accel || s == State::Run || s == State::Decel;
    }

    bool limitMin() const
    {
        if (!cfg_.limits_enabled) return false;
        const bool r = Hw::limitMinRaw();
        return cfg_.lim_invert ? !r : r;
    }

    bool limitMax() const
    {
        if (!cfg_.limits_enabled) return false;
        const bool r = Hw::limitMaxRaw();
        return cfg_.lim_invert ? !r : r;
    }

    bool directionBlocked(std::int8_t d) const
    {
        return (d < 0 && limitMin()) || (d > 0 && limitMax());
    }

    Error precheck() const
    {
        if (state_ == State::Fault) return Error::Fault;
        if (!enabled())             return Error::Disabled;
        if (Hw::estopRaw())         return Error::Fault;
        return Error::Ok;
    }

    Error checkSoft(Steps target) const
    {
        if (!cfg_.soft_limits) return Error::Ok;
        const float mm = scale_.toMm(target).raw();
        if (mm < cfg_.soft_min_mm - 1e-3f || mm > cfg_.soft_max_mm + 1e-3f) return Error::Range;
        return Error::Ok;
    }

    std::uint32_t periodFor(MmPerSec v) const
    {
        const float v_steps = scale_.toStepsPerSec(v);
        std::uint32_t c = static_cast<std::uint32_t>(
            std::ceil(static_cast<double>(kTimerHz) / v_steps));
        return c < 4 ? 4u : c;
    }

    void finish(std::uint32_t evt)
    {
        Hw::timerStop();
        state_ = State::Idle;
        events_ |= evt;
    }

    /**
     * cn+1 = cn − (2·cn + rest) / (4n + 1)
     *
     * The division remainder is carried into the next step. Without it, at
     * large n the numerator becomes smaller than the denominator, the integer
     * part collapses to zero, and acceleration silently stalls below the
     * requested speed.
     */
    void rampNext()
    {
        std::int64_t den = 4LL * n_ + 1;
        if (den == 0) den = 1;

        const std::int64_t num = 2LL * static_cast<std::int64_t>(c_) + rest_;
        const std::int64_t d   = num / den;
        rest_ = static_cast<std::int32_t>(num % den);

        std::int64_t nc = static_cast<std::int64_t>(c_) - d;
        if (nc < 2)             nc = 2;
        if (nc > 0xFFFFFFFELL)  nc = 0xFFFFFFFELL;
        c_ = static_cast<std::uint32_t>(nc);
    }

    void launchPosition(Steps target)
    {
        const Steps delta = target - pos_;
        if (delta == Steps{}) { events_ |= Event::Done; return; }

        const std::int8_t d = static_cast<std::int8_t>(delta.sign());
        if (directionBlocked(d)) { events_ |= Event::Limit; return; }

        {
            CriticalSection cs;
            dir_       = d;
            target_    = target;
            remaining_ = static_cast<std::uint32_t>(delta.abs().raw());
            mode_      = Mode::Position;
            n_         = 0;
            rest_      = 0;
            decel_to_stop_ = false;
            c_     = (c0_ > c_min_) ? c0_ : c_min_;
            state_ = (c_ > c_min_) ? State::Accel : State::Run;
        }
        Hw::setDirectionPin(cfg_.dir_invert ? (d < 0) : (d > 0));
        Hw::dirSetupDelay();
        Hw::timerStart(c_);
    }

    void launchVelocity(MmPerSec v)
    {
        if (v.magnitude() < 1e-4f) { stop(); return; }
        const std::int8_t d = static_cast<std::int8_t>(v.sign());
        if (directionBlocked(d)) { events_ |= Event::Limit; return; }

        const std::uint32_t cmin = periodFor(v);
        {
            CriticalSection cs;
            dir_   = d;
            mode_  = Mode::Velocity;
            c_min_ = cmin;
            n_     = 0;
            rest_  = 0;
            decel_to_stop_ = false;
            c_     = (c0_ > cmin) ? c0_ : cmin;
            state_ = (c_ > cmin) ? State::Accel : State::Run;
        }
        Hw::setDirectionPin(cfg_.dir_invert ? (d < 0) : (d > 0));
        Hw::dirSetupDelay();
        Hw::timerStart(c_);
    }

    void homeService()
    {
        if (home_phase_ == 0 || motionBusy()) return;

        switch (home_phase_) {
        case 1:                                     // fast approach finished
            if (!limitMin()) { home_phase_ = 0; state_ = State::Fault;
                               events_ |= Event::Fault; return; }
            flags_ &= ~Flag::LimMin;
            home_phase_ = 2;
            launchPosition(pos_ + scale_.toSteps(Mm{cfg_.home_backoff_mm}));
            break;

        case 2:                                     // back-off finished
            home_phase_ = 3;
            launchVelocity(MmPerSec{-cfg_.home_v_mms / 5.0f});
            break;

        case 3:
            if (!limitMin()) { home_phase_ = 0; state_ = State::Fault;
                               events_ |= Event::Fault; return; }
            flags_ &= ~Flag::LimMin;
            home_phase_ = 0;
            state_ = State::Idle;
            zero();
            events_ |= Event::Homed | Event::Done;
            break;

        default: break;
        }
    }

    // ---- state ----
    Config cfg_{};
    Scale  scale_{};

    volatile State state_{State::Idle};
    volatile Mode  mode_{Mode::Position};

    Steps pos_{};
    Steps target_{};
    volatile std::uint32_t remaining_{};
    volatile std::int8_t   dir_{1};

    volatile std::uint32_t c_{}, c0_{}, c_min_{};
    volatile std::int32_t  n_{};
    volatile std::int32_t  rest_{};
    volatile bool decel_to_stop_{false};

    volatile std::uint32_t flags_{};
    volatile std::uint32_t events_{};
    volatile std::uint32_t ms_{};

    std::uint32_t pulse_ticks_{30};
    volatile std::uint8_t home_phase_{0};

    Pending  pending_{Pending::None};
    Steps    pending_target_{};
    MmPerSec pending_vel_{};
};

}  // namespace stepctl